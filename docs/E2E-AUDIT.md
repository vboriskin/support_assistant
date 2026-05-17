# E2E-аудит UI через Playwright

`scripts/e2e_audit.py` — browser-аудит UI. Headless Chromium проходит по
20 вкладкам, выполняет 7 ключевых пользовательских сценариев, ловит
console errors / network failures / HTTP-статусы. На CI запускается в job
`e2e (Playwright)` на каждый PR.

## Запуск локально

```bash
# 1. Установить (один раз)
pip install -e ".[dev,e2e]"
playwright install chromium

# 2. Поднять стенд (нужно для аудита)
./run_demo.sh --port 8765 --no-prompt &

# 3. Прогнать
python -m scripts.e2e_audit --base-url http://127.0.0.1:8765
```

После прогона:
- `/tmp/sa_audit_report.json` — полный отчёт.
- `/tmp/sa_audit_screenshots/` — PNG провалившихся страниц.

## Что проверяется

### 20 маршрутов UI

Для каждой вкладки скрипт:
1. Делает `page.goto(f"{ui}#{route}", wait_until="networkidle")`.
2. Ждёт SPA-рендеринг (500ms timeout).
3. Проверяет, что в `<main>` есть контент (>30 символов) и есть `<h1>`.
4. Замеряет latency.
5. При failure — делает скриншот всей страницы.

### 7 ключевых сценариев

| Сценарий | Что делает |
|---|---|
| `assistant_chat` | Открывает /assistant, отправляет вопрос, ждёт ≥2 bubble (user + assistant) и не-пустой текст ответа через SSE. |
| `tickets_list` | Открывает /tickets, проверяет что в таблице есть строки, кликает первую → проверяет появление detail-панели. |
| `pii_playground` | Открывает /pii, вставляет тестовый текст с PII, жмёт «Замаскировать», проверяет что в выводе есть `<` и `>` (маркеры маскированных полей). |
| `settings_diag` | Проверяет видимость кнопки «Выгрузить логи» + дёргает `GET /api/diag` напрямую, проверяет HTTP 200 и size > 1 КБ. |
| `kb_page` | Проверяет видимость кнопок «Новая статья» и «Импорт zip / md». |
| `health_details` | Дёргает `/api/stats/health-details`, проверяет overall_status и список адаптеров. |
| `coverage` | Дёргает `/api/stats/coverage`, фиксирует tickets_total / summaries_total / kb_total. |

### Глобальные счётчики

- **Console errors / warnings** — на всех страницах. По 1 errorу — fail.
- **Network failures** — все ответы с status ≥ 400.
- **API calls** — все запросы к `/api/*` с их статусами.

## Критерии падения CI

Скрипт возвращает exit-code `1`, если выполняется хотя бы одно:

1. Хотя бы один из 20 маршрутов не открылся (main пустой или нет `<h1>`).
2. Хотя бы один из 7 ключевых сценариев упал.
3. Любой 5xx в `network_failures`.
4. Хотя бы одна console error (не warning).

С флагом `--strict` дополнительно:
- Любая console warning.
- Любой 4xx (включая 401/403/404).

В CI strict-mode включается через `workflow_dispatch` с галкой
«Strict mode» (см. [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Отчёт — структура JSON

```json
{
  "base_url": "http://127.0.0.1:8765",
  "started_at": "2026-05-17T12:00:00Z",
  "routes": [
    {"route": "/dashboard", "title": "Сводка", "ok": true,
     "main_text_len": 1234, "h1": "Сводка", "latency_ms": 247}
  ],
  "console_errors": [],
  "network_failures": [],
  "api_calls": [
    {"status": 200, "url": "...", "method": "GET"}
  ],
  "page_load_failures": [],
  "key_flow_results": {
    "assistant_chat": {"ok": true, "bubbles_count": 2,
                       "answer_preview": "..."},
    "tickets_list": {"ok": true, "rows_count": 50, "detail_panel": true},
    ...
  },
  "screenshots": ["/tmp/sa_audit_screenshots/fail_costs.png"],
  "summary": {
    "routes_total": 20, "routes_ok": 20, "routes_failed": 0,
    "api_calls_total": 28, "api_status_dist": {"200": 28},
    "console_errors_total": 0, "console_error_types": {},
    "key_flows_ok": 7, "key_flows_total": 7
  }
}
```

## Как добавить новую вкладку

1. Добавь маршрут в `ROUTES` в `scripts/e2e_audit.py`:
   ```python
   ROUTES = [
       ...
       ("/myroute", "Заголовок"),
   ]
   ```
2. Если на вкладке есть форма с побочным эффектом — допиши сценарий
   рядом с остальными (после `# 2.7 Coverage`).
3. Прогони локально, убедись что зелёное.
4. Push → CI должен пройти.

## CI: что в артефакте

Job `e2e (Playwright)` всегда (`if: always()`) аплоадит артефакт
`e2e-report-<run_id>` со следующими файлами:

- `sa_audit_report.json` — отчёт.
- `sa_audit_screenshots/*.png` — скриншоты провалов.
- `uvicorn.log` — логи бекенда за время прогона.

Retention — 14 дней. Скачивается с GitHub Actions → конкретный run → секция
«Artifacts» внизу.

## Время прогона

| Стадия | Холодный кэш | Тёплый кэш |
|---|---|---|
| `pip install -e ".[dev,e2e]"` | 2–3 мин | 20с (pip-cache) |
| `playwright install chromium` | 30с | 0с (actions/cache) |
| `init_db + ingest 200 тикетов` | 5–10с | 5–10с |
| Аудит (20 страниц + 7 сценариев) | 40–60с | 40–60с |
| **Итого** | **3–4 мин** | **~1.5 мин** |

## Если e2e станет узким местом

В `tests.yml` готов рецепт в комментарии — триггерить e2e только при
PR-метке `e2e`:

```yaml
on:
  pull_request:
    types: [labeled, synchronize]
jobs:
  e2e:
    if: contains(github.event.pull_request.labels.*.name, 'e2e')
```

Тогда push в `main` и `workflow_dispatch` оставляем как есть, а на PR
e2e будет запускаться только когда ревьюер поставит метку `e2e`.
