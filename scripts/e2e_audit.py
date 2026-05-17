"""E2E-аудит UI Support Assistant через Playwright (Chromium headless).

Проходит по всем UI-маршрутам, ловит console-errors / network-failures /
HTTP-статусы, выполняет ключевые пользовательские сценарии (чат с ассистентом,
открытие тикета, PII-маскирование, выгрузка диагностики).

Запуск локально:
    # 1. Поднять сервер (например, demo-режим)
    ./run_demo.sh --port 8765 --no-prompt &
    # 2. Дождаться готовности и прогнать аудит
    python -m scripts.e2e_audit --base-url http://127.0.0.1:8765

Запуск в CI: см. .github/workflows/tests.yml — job ``e2e``.

Артефакты пишутся в:
    --report-out  /tmp/sa_audit_report.json   — JSON со всеми деталями
    --screenshot-dir /tmp/sa_audit_screenshots/  — PNG при failure

Exit code:
    0 — все маршруты ok, все ключевые сценарии прошли, нет 4xx/5xx, нет console errors
    1 — найдено хотя бы одно из выше
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

# 20 UI-вкладок проекта (см. ui/index.html)
ROUTES: list[tuple[str, str]] = [
    ("/dashboard", "Сводка"),
    ("/assistant", "Ассистент"),
    ("/tickets", "Тикеты"),
    ("/history", "История"),
    ("/kb", "База знаний"),
    ("/ingest", "Ингест"),
    ("/weak", "Слабые ответы"),
    ("/evals", "Evals"),
    ("/health", "Здоровье"),
    ("/costs", "Стоимость"),
    ("/prompts", "Промпты"),
    ("/fewshot", "Few-shot"),
    ("/stale", "Устаревшее KB"),
    ("/pii", "PII playground"),
    ("/audit", "Аудит"),
    ("/alerts", "Алёрты"),
    ("/artifacts", "Артефакты"),
    ("/instructions", "Инструкция"),
    ("/settings", "Настройки"),
    ("/description", "Описание"),
]

_API_RX = re.compile(r"^https?://[^/]+/api/")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8765",
                    help="Базовый URL поднятого приложения")
    ap.add_argument("--report-out", default="/tmp/sa_audit_report.json")
    ap.add_argument("--screenshot-dir", default="/tmp/sa_audit_screenshots")
    ap.add_argument("--strict", action="store_true",
                    help="Возвращать non-zero exit при любых console-warnings и 4xx")
    return ap.parse_args()


def run_audit(base_url: str, report_out: Path, screenshot_dir: Path) -> dict:
    ui = f"{base_url}/ui"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "base_url": base_url,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "routes": [],
        "console_errors": [],
        "network_failures": [],
        "api_calls": [],
        "page_load_failures": [],
        "key_flow_results": {},
        "screenshots": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent="SA-E2E-Audit/1.0",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        # ── Слушатели на каждую страницу ──
        def on_console(msg):
            if msg.type in ("error", "warning"):
                report["console_errors"].append(
                    {"type": msg.type, "text": msg.text[:300], "url": page.url}
                )

        def on_response(resp):
            try:
                url = resp.url
                status = resp.status
            except Exception:  # noqa: BLE001
                return
            if _API_RX.match(url):
                report["api_calls"].append(
                    {"status": status, "url": url, "method": resp.request.method}
                )
            if status >= 400:
                report["network_failures"].append(
                    {"status": status, "url": url, "method": resp.request.method}
                )

        page.on("console", on_console)
        page.on("response", on_response)

        # ── 1. Проход по всем маршрутам ──
        for route, title in ROUTES:
            entry = {"route": route, "title": title, "ok": False}
            t0 = time.time()
            try:
                page.goto(f"{ui}#{route}", wait_until="networkidle", timeout=15000)
                page.wait_for_timeout(500)  # SPA отрисовка
                main_text = page.locator("main").inner_text(timeout=5000)
                h1 = page.locator("h1").first.inner_text(timeout=2000)
                entry["main_text_len"] = len(main_text)
                entry["h1"] = h1[:80]
                entry["ok"] = bool(main_text and len(main_text) > 30)
            except Exception as e:
                entry["error"] = str(e)[:300]
                report["page_load_failures"].append({"route": route, "error": str(e)[:300]})
                # Скриншот для CI-артефакта
                try:
                    fname = screenshot_dir / f"fail{route.replace('/', '_')}.png"
                    page.screenshot(path=str(fname), full_page=True)
                    report["screenshots"].append(str(fname))
                except Exception:  # noqa: BLE001
                    pass
            entry["latency_ms"] = int((time.time() - t0) * 1000)
            report["routes"].append(entry)

        # ── 2. Ключевые пользовательские сценарии ──

        # 2.1 Ассистент — задать вопрос, получить SSE-ответ
        try:
            page.goto(f"{ui}#/assistant", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(500)
            page.locator("textarea[name='query']").fill(
                "Не загружается скан паспорта, файл 12 МБ"
            )
            page.locator("form[data-slot='composer'] button[type='submit']").click()
            page.wait_for_selector("[data-slot='history'] .message", timeout=10000)
            page.wait_for_timeout(3000)  # SSE стрим
            bubbles = page.locator("[data-slot='history'] .message").count()
            text = page.locator("[data-slot='history'] [data-slot='text']").last.inner_text()
            report["key_flow_results"]["assistant_chat"] = {
                "ok": bubbles >= 2,
                "bubbles_count": bubbles,
                "answer_preview": text[:200],
            }
        except Exception as e:
            report["key_flow_results"]["assistant_chat"] = {"ok": False, "error": str(e)[:300]}

        # 2.2 Тикеты — список + детальная панель
        try:
            page.goto(f"{ui}#/tickets", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(500)
            rows = page.locator("table tbody tr")
            cnt = rows.count()
            detail = False
            if cnt > 0:
                rows.first.click()
                page.wait_for_timeout(500)
                detail = page.locator("[data-slot='detail']").count() > 0
            report["key_flow_results"]["tickets_list"] = {
                "ok": cnt > 0,
                "rows_count": cnt,
                "detail_panel": detail,
            }
        except Exception as e:
            report["key_flow_results"]["tickets_list"] = {"ok": False, "error": str(e)[:300]}

        # 2.3 PII playground — backend POST через UI
        try:
            page.goto(f"{ui}#/pii", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(500)
            page.locator("[data-slot='input']").fill(
                "Иван Петров, +7 905 123-45-67, ivan@example.com, APP-12345678"
            )
            page.locator("[data-slot='run']").click()
            page.wait_for_timeout(2000)
            masked = page.locator("[data-slot='masked']").inner_text()
            report["key_flow_results"]["pii_playground"] = {
                "ok": "<" in masked and ">" in masked,
                "masked_preview": masked[:300],
            }
        except Exception as e:
            report["key_flow_results"]["pii_playground"] = {"ok": False, "error": str(e)[:300]}

        # 2.4 Настройки — кнопка «Выгрузить логи» + сам /api/diag
        try:
            page.goto(f"{ui}#/settings", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(500)
            btn = page.locator("[data-slot='download-diag']")
            visible = btn.count() > 0 and btn.is_visible()
            resp = context.request.get(f"{base_url}/api/diag", headers={"X-User-Id": "audit"})
            size = len(resp.body()) if resp.ok else 0
            report["key_flow_results"]["settings_diag"] = {
                "ok": visible and resp.ok and size > 1000,
                "button_visible": visible,
                "diag_http": resp.status,
                "diag_size_bytes": size,
            }
        except Exception as e:
            report["key_flow_results"]["settings_diag"] = {"ok": False, "error": str(e)[:300]}

        # 2.5 KB — две кнопки (Новая статья + Импорт zip)
        try:
            page.goto(f"{ui}#/kb", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(500)
            new_btn = page.locator("[data-slot='new-btn']").count()
            bulk_btn = page.locator("[data-slot='bulk-btn']").count()
            report["key_flow_results"]["kb_page"] = {
                "ok": new_btn > 0 and bulk_btn > 0,
                "buttons": {"new": new_btn, "bulk": bulk_btn},
            }
        except Exception as e:
            report["key_flow_results"]["kb_page"] = {"ok": False, "error": str(e)[:300]}

        # 2.6 Health-details — пинги адаптеров
        try:
            resp = context.request.get(
                f"{base_url}/api/stats/health-details", headers={"X-User-Id": "audit"}
            )
            data = resp.json() if resp.ok else {}
            report["key_flow_results"]["health_details"] = {
                "ok": resp.ok,
                "overall_status": data.get("status"),
                "checks": [
                    {"name": c.get("name"), "status": c.get("status")}
                    for c in data.get("checks", [])
                ],
            }
        except Exception as e:
            report["key_flow_results"]["health_details"] = {"ok": False, "error": str(e)[:300]}

        # 2.7 Coverage
        try:
            resp = context.request.get(
                f"{base_url}/api/stats/coverage", headers={"X-User-Id": "audit"}
            )
            data = resp.json() if resp.ok else {}
            report["key_flow_results"]["coverage"] = {
                "ok": resp.ok,
                "tickets_total": data.get("tickets_total"),
                "summaries_total": data.get("summaries_total"),
                "kb_total": data.get("kb_total"),
            }
        except Exception as e:
            report["key_flow_results"]["coverage"] = {"ok": False, "error": str(e)[:300]}

        browser.close()

    # Агрегаты
    api_dist = Counter(c["status"] for c in report["api_calls"])
    net_dist = Counter(c["status"] for c in report["network_failures"])
    routes_ok = sum(1 for r in report["routes"] if r["ok"])
    err_types = Counter(e["type"] for e in report["console_errors"])
    flows_ok = sum(1 for v in report["key_flow_results"].values() if v.get("ok"))

    report["summary"] = {
        "routes_total": len(ROUTES),
        "routes_ok": routes_ok,
        "routes_failed": len(ROUTES) - routes_ok,
        "api_calls_total": len(report["api_calls"]),
        "api_status_dist": dict(api_dist),
        "network_failures_total": len(report["network_failures"]),
        "network_status_dist": dict(net_dist),
        "console_errors_total": len(report["console_errors"]),
        "console_error_types": dict(err_types),
        "key_flows_ok": flows_ok,
        "key_flows_total": len(report["key_flow_results"]),
    }

    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    args = _parse_args()
    report_out = Path(args.report_out)
    screenshot_dir = Path(args.screenshot_dir)

    print(f"E2E audit: {args.base_url}")
    print(f"  report:      {report_out}")
    print(f"  screenshots: {screenshot_dir}/")
    print()

    try:
        report = run_audit(args.base_url, report_out, screenshot_dir)
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    s = report["summary"]
    print("=" * 60)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print("=" * 60)

    # Критерии падения CI:
    # 1) Хотя бы один маршрут не открылся.
    # 2) Хотя бы один ключевой сценарий упал.
    # 3) Любой 5xx в network failures.
    # 4) Console errors > 0 (errors, не warnings).
    # 5) В strict-режиме — также console warnings и 4xx.
    failed = False
    if s["routes_failed"] > 0:
        print(f"FAIL: {s['routes_failed']} route(s) did not load", file=sys.stderr)
        failed = True
    if s["key_flows_ok"] < s["key_flows_total"]:
        print(
            f"FAIL: {s['key_flows_total'] - s['key_flows_ok']} key flow(s) broken",
            file=sys.stderr,
        )
        failed = True
    has_5xx = any(code >= 500 for code in s["network_status_dist"])
    if has_5xx:
        print(f"FAIL: 5xx in network: {s['network_status_dist']}", file=sys.stderr)
        failed = True
    if s["console_error_types"].get("error", 0) > 0:
        print(
            f"FAIL: {s['console_error_types']['error']} console error(s)",
            file=sys.stderr,
        )
        failed = True
    if args.strict:
        if s["console_error_types"].get("warning", 0) > 0:
            print(
                f"FAIL (strict): {s['console_error_types']['warning']} console warning(s)",
                file=sys.stderr,
            )
            failed = True
        has_4xx = any(400 <= code < 500 for code in s["network_status_dist"])
        if has_4xx:
            print(f"FAIL (strict): 4xx in network: {s['network_status_dist']}",
                  file=sys.stderr)
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
