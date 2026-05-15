"""GET /api/diag — диагностический дамп для локализации проблем.

Что собирается:
  - версии Python и ключевых библиотек;
  - текущие настройки (секреты маскированы);
  - схема БД: список таблиц + кол-во строк;
  - health-чек адаптеров (LLM, embeddings, vector_store, FTS);
  - coverage по модулям;
  - последние audit-записи (без тел запросов);
  - последние ingest-jobs;
  - сводка llm-вызовов за 24 часа (без полных prompt'ов);
  - последние LLM-ошибки (короткий summary);
  - последние evals-прогоны (только summary);
  - содержимое статистики (период week).

Что НЕ собирается:
  - полные тексты prompt/response (только хэши и preview ≤200 симв.);
  - PII клиентов (фамилии, паспорта, телефоны — даже если попадутся в preview, они уже замаскированы PII-pipeline'ом на ингесте);
  - значения secret-полей настроек (показываются маской `****1234`);
  - содержимое .env-файла (только имена + наличие).

Использование: GET /api/diag → скачивается JSON с заголовком
`Content-Disposition: attachment` под именем `diag_YYYY-MM-DD_HHMMSS.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import desc, func, select, text

from api.dependencies import (
    SessionDep,
    embeddings_client,
    llm_client,
    settings_dep,
    text_search_client,
    vector_store_client,
)
from config.logging import get_logger
from config.settings import Settings
from db.models import (
    AuditLog,
    Conversation,
    FewShotExample,
    IngestJob,
    KBArticle,
    LLMCallLog,
    Message,
    PromptVersion,
    Ticket,
    TicketSummary,
)

logger = get_logger("api.diag")
router = APIRouter(prefix="/diag", tags=["diag"])


# --- Маскирование значений --------------------------------------------------


def _mask(v: Any) -> str:
    """Превратить «секретное» значение в `****1234`-маску."""
    s = str(v or "")
    if not s:
        return ""
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


def _hash(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:16]


# --- Версии библиотек ------------------------------------------------------


def _versions() -> dict[str, str]:
    out: dict[str, str] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for mod in (
        "fastapi", "uvicorn", "sqlalchemy", "alembic", "aiosqlite",
        "pydantic", "pydantic_settings", "httpx", "sentence_transformers",
        "transformers", "torch", "natasha", "structlog", "greenlet",
    ):
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", None) or getattr(m, "version", None)
            out[mod] = str(ver) if ver else "unknown"
        except Exception:  # noqa: BLE001
            out[mod] = "not installed"
    return out


# --- Настройки (с маскированными секретами) --------------------------------


def _settings_snapshot(s: Settings) -> dict[str, Any]:
    return {
        "app_env": s.app_env,
        "app_host": s.app_host,
        "app_port": s.app_port,
        "log_level": s.log_level,
        "log_format": s.log_format,
        "db": {
            "backend": s.db.backend,
            "sqlite_path": str(s.db.sqlite_path),
            "postgres_host": s.db.postgres_host,
            "postgres_db": s.db.postgres_db,
            "postgres_user": s.db.postgres_user,
            "postgres_password_set": bool(s.db.postgres_password.get_secret_value()),
        },
        "llm": {
            "provider": s.llm.provider,
            "timeout_seconds": s.llm.timeout_seconds,
            "max_retries": s.llm.max_retries,
            "budget_per_user_daily": s.llm.budget_per_user_daily,
        },
        "gigachat": {
            "base_url": s.gigachat.base_url,
            "auth_url": s.gigachat.auth_url,
            "scope": s.gigachat.scope,
            "model_primary": s.gigachat.model_primary,
            "model_judge": s.gigachat.model_judge,
            "verify_ssl": s.gigachat.verify_ssl,
            "ca_bundle_path": str(s.gigachat.ca_bundle_path or ""),
            "client_id_mask": _mask(s.gigachat.client_id.get_secret_value()),
            "client_secret_set": bool(s.gigachat.client_secret.get_secret_value()),
        },
        "openai_compat": {
            "base_url": s.openai_compat.base_url,
            "model": s.openai_compat.model,
            "api_key_set": bool(s.openai_compat.api_key.get_secret_value()),
        },
        "embeddings": {
            "provider": s.embeddings.provider,
            "model_name": s.embeddings.model_name,
            "device": s.embeddings.device,
            "batch_size": s.embeddings.batch_size,
            "dimension": s.embeddings.dimension,
            "api_url": s.embeddings.api_url or "",
            "api_key_set": bool(s.embeddings.api_key.get_secret_value()),
            "cache_dir": str(s.embeddings.cache_dir),
        },
        "vector_store": {
            "backend": s.vector_store.backend,
            "search_top_k": s.vector_store.search_top_k,
            "text_search_top_k": s.vector_store.text_search_top_k,
        },
        "retrieval": s.retrieval.model_dump(),
        "reranker": s.reranker.model_dump(),
        "pii": s.pii.model_dump(),
        "ingest": s.ingest.model_dump(),
        "integrations": s.integrations.model_dump(),
        "security": {
            "rate_limit_per_minute": s.security.rate_limit_per_minute,
            "max_body_bytes": s.security.max_body_bytes,
            "csrf_enabled": s.security.csrf_enabled,
            "allowed_llm_hosts": s.security.allowed_llm_hosts,
            "db_audit_enabled": s.security.db_audit_enabled,
        },
        "alerts": {
            "enabled": s.alerts.enabled,
            "webhook_url_set": bool(s.alerts.webhook_url),
            "p95_latency_threshold_ms": s.alerts.p95_latency_threshold_ms,
            "no_sources_ratio_threshold": s.alerts.no_sources_ratio_threshold,
            "error_count_threshold": s.alerts.error_count_threshold,
            "check_interval_sec": s.alerts.check_interval_sec,
        },
        "ui": s.ui.model_dump(),
    }


# --- Что в .env ------------------------------------------------------------


def _env_summary() -> dict[str, Any]:
    """Имена env-переменных, которые подхватились. Значения — только bool/len."""
    env_file = os.getenv("ENV_FILE", ".env")
    out: dict[str, Any] = {"env_file": env_file, "env_file_exists": False, "keys": []}
    try:
        if os.path.exists(env_file):
            out["env_file_exists"] = True
            with open(env_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    out["keys"].append(
                        {"name": key.strip(), "length": len(val.strip().strip("\"'"))}
                    )
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


# --- Адаптеры (health) -----------------------------------------------------


async def _check_adapter(name: str, fn) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    import time as _t

    t0 = _t.time()
    try:
        await fn()
        return {"name": name, "status": "ok", "latency_ms": int((_t.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "status": "error", "error": str(e)[:400]}


# --- БД-снимок --------------------------------------------------------------


async def _db_snapshot(session) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Кол-во строк по основным таблицам + миграции."""
    counts: dict[str, int] = {}
    for label, model in [
        ("tickets", Ticket),
        ("ticket_summaries", TicketSummary),
        ("kb_articles", KBArticle),
        ("conversations", Conversation),
        ("messages", Message),
        ("llm_call_logs", LLMCallLog),
        ("ingest_jobs", IngestJob),
        ("audit_log", AuditLog),
        ("prompt_versions", PromptVersion),
        ("few_shot_examples", FewShotExample),
    ]:
        try:
            n = (await session.execute(select(func.count()).select_from(model))).scalar() or 0
            counts[label] = int(n)
        except Exception as e:  # noqa: BLE001
            counts[label] = -1
            counts[label + "__error"] = str(e)[:200]  # type: ignore[assignment]

    # Alembic revision
    rev = None
    try:
        rev = (await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar()
    except Exception:  # noqa: BLE001
        rev = None

    return {"table_counts": counts, "alembic_version": rev}


# --- Audit / LLM logs / ingest jobs ----------------------------------------


async def _recent_audit(session, limit: int = 50) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    rows = (
        await session.execute(
            select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "user_id": r.user_id,
            "action": r.action,
            "method": r.method,
            "path": r.path,
            "status": r.status,
            "target_type": r.target_type,
            "target_id": r.target_id,
        }
        for r in rows
    ]


async def _recent_llm_calls(session, limit: int = 50) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    """Безопасное summary вызовов LLM: только preview ≤200 символов и хэши."""
    rows = (
        await session.execute(
            select(LLMCallLog).order_by(desc(LLMCallLog.created_at)).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "user_id": r.user_id,
            "purpose": r.purpose,
            "model": r.model,
            "prompt_hash": r.prompt_hash,
            "prompt_preview": (r.prompt_preview or "")[:200],
            "response_preview": (r.response_preview or "")[:200],
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "latency_ms": r.latency_ms,
            "error": (r.error or "")[:400] or None,
        }
        for r in rows
    ]


async def _llm_24h_summary(session) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from_dt = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    rows = (
        await session.execute(
            select(
                LLMCallLog.purpose,
                LLMCallLog.error,
                LLMCallLog.latency_ms,
                LLMCallLog.prompt_tokens,
                LLMCallLog.completion_tokens,
            ).where(LLMCallLog.created_at >= from_dt)
        )
    ).all()
    by_purpose: dict[str, dict[str, Any]] = {}
    for purpose, err, lat, pt, ct in rows:
        b = by_purpose.setdefault(
            purpose or "unknown",
            {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "latencies": []},
        )
        b["calls"] += 1
        if err:
            b["errors"] += 1
        b["prompt_tokens"] += int(pt or 0)
        b["completion_tokens"] += int(ct or 0)
        b["latencies"].append(float(lat or 0))
    out: list[dict[str, Any]] = []
    for purpose, b in by_purpose.items():
        lats = sorted(b.pop("latencies"))
        n = len(lats)
        b["avg_latency_ms"] = int(sum(lats) / n) if n else 0
        b["p95_latency_ms"] = int(lats[max(0, int(0.95 * n) - 1)]) if n else 0
        b["purpose"] = purpose
        out.append(b)
    return {"window_hours": 24, "by_purpose": sorted(out, key=lambda x: -x["calls"])}


async def _recent_ingest_jobs(session, limit: int = 20) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    rows = (
        await session.execute(
            select(IngestJob).order_by(desc(IngestJob.created_at)).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "type": r.job_type,
            "status": r.status,
            "total": r.total_items,
            "processed": r.processed_items,
            "failed": r.failed_items,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error_message": (r.error_message or "")[:300] or None,
            "metadata": r.metadata_json,
        }
        for r in rows
    ]


# --- Eval-прогоны (метаданные) ---------------------------------------------


def _recent_eval_runs(limit: int = 10) -> list[dict[str, Any]]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "evals" / "reports"
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "run_id": data.get("run_id"),
                    "case_set": data.get("case_set"),
                    "started_at": data.get("started_at"),
                    "finished_at": data.get("finished_at"),
                    "total_cases": data.get("total_cases"),
                    "completed_cases": data.get("completed_cases"),
                    "aggregate": data.get("aggregate") or {},
                }
            )
        except Exception as e:  # noqa: BLE001
            out.append({"file": p.name, "error": str(e)[:200]})
    return out


# --- Main endpoint ----------------------------------------------------------


@router.get("")
async def diag(
    session: SessionDep,
    settings: Annotated[Settings, Depends(settings_dep)],
) -> Response:
    """Полный диагностический дамп. JSON-файл, помеченный как attachment."""
    # Параллельно гоним health-чеки
    llm = llm_client()
    emb = embeddings_client()
    vec = vector_store_client()
    fts = text_search_client()

    from adapters.llm.base import ChatMessage

    async def _llm_ping():
        await llm.chat_completion(
            messages=[ChatMessage(role="user", content="ping")],
            temperature=0.0,
            max_tokens=1,
        )

    health = [
        await _check_adapter("llm", _llm_ping),
        await _check_adapter("embeddings", lambda: emb.embed_query("ping")),
        await _check_adapter("vector_store", lambda: vec.health()),
        await _check_adapter("text_search", lambda: fts.count()),
    ]

    out = {
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        "app_version": "0.1.0",
        "versions": _versions(),
        "env_file": _env_summary(),
        "settings": _settings_snapshot(settings),
        "db": await _db_snapshot(session),
        "health_checks": health,
        "llm_24h_summary": await _llm_24h_summary(session),
        "recent_audit": await _recent_audit(session, limit=50),
        "recent_llm_calls": await _recent_llm_calls(session, limit=50),
        "recent_ingest_jobs": await _recent_ingest_jobs(session, limit=20),
        "recent_eval_runs": _recent_eval_runs(limit=10),
        "notes": [
            "PII клиентов не попадает в этот дамп — на ингесте всё уже маскируется. "
            "В preview LLM-вызовов есть фрагменты текста, уже после маскирования.",
            "Секреты (client_secret, API keys, webhook URLs) показаны как маска '****XXXX' "
            "или флагом *_set: true|false.",
            "Этот файл можно отправлять команде разработки.",
        ],
    }

    body = json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8")
    ts = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d_%H%M%S")
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="diag_{ts}.json"',
            "Cache-Control": "no-store",
        },
    )
