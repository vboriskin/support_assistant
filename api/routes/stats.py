"""Dashboard /api/stats/dashboard.

- Параметр ``period`` ∈ {day, week, month}: окно для timeseries и aggregates.
- Timeseries: тикеты по дням за период (для line-чарта).
- p95 latency: вычисляется в Python (точные перцентили в SQLite через
  оконные функции трудоёмки; на 100к строк — приемлемо).
- Anomalies: модули, у которых текущая неделя ≥ +30% к предыдущей.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from api.dependencies import SessionDep
from db.models import IngestJob, LLMCallLog
from db.models import Ticket as TicketORM
from db.models import TicketSummary as TicketSummaryORM

router = APIRouter(prefix="/stats", tags=["stats"])

_DAYS = {"day": 1, "week": 7, "month": 30}


def _period_days(p: str) -> int:
    return _DAYS.get(p, 7)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _date_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _p95(values: list[float]) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(0.95 * len(s)) - 1))
    return int(s[k])


# ----------------------------------------------------------------------
# Cost & latency для LLM
# ----------------------------------------------------------------------


@router.get("/llm-costs")
async def llm_costs(
    session: SessionDep,
    period: Literal["day", "week", "month"] = Query(default="week"),
) -> dict[str, Any]:
    """Стоимость и латентность вызовов LLM за период.

    Группировка — по ``purpose``. Если ``token_usage`` пустой (mock-llm), все
    значения по токенам — нули; UI это показывает корректно.
    """
    days = _period_days(period)
    now = _now()
    from_dt = now - timedelta(days=days)

    rows = (
        await session.execute(
            select(
                LLMCallLog.purpose,
                LLMCallLog.model,
                LLMCallLog.prompt_tokens,
                LLMCallLog.completion_tokens,
                LLMCallLog.latency_ms,
                LLMCallLog.error,
                LLMCallLog.created_at,
            ).where(LLMCallLog.created_at >= from_dt)
        )
    ).all()

    by_purpose: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    for d in range(days):
        by_day[_date_key(from_dt + timedelta(days=d))] = {"date": "", "calls": 0, "tokens": 0, "errors": 0}
    for key in by_day:
        by_day[key]["date"] = key

    total_tokens_prompt = 0
    total_tokens_completion = 0
    total_calls = 0
    total_errors = 0
    all_latencies: list[float] = []

    for purpose, model, pt, ct, lat, err, created_at in rows:
        total_calls += 1
        if err:
            total_errors += 1
        pt_i = int(pt or 0)
        ct_i = int(ct or 0)
        total_tokens_prompt += pt_i
        total_tokens_completion += ct_i
        all_latencies.append(float(lat or 0))

        bucket = by_purpose.setdefault(
            purpose or "unknown",
            {
                "purpose": purpose or "unknown",
                "model": model,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "errors": 0,
                "latencies": [],
            },
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += pt_i
        bucket["completion_tokens"] += ct_i
        bucket["latencies"].append(float(lat or 0))
        if err:
            bucket["errors"] += 1

        dkey = _date_key(created_at)
        if dkey in by_day:
            by_day[dkey]["calls"] += 1
            by_day[dkey]["tokens"] += pt_i + ct_i
            if err:
                by_day[dkey]["errors"] += 1

    purposes_out: list[dict[str, Any]] = []
    for v in by_purpose.values():
        lats = v.pop("latencies")
        v["avg_latency_ms"] = int(sum(lats) / len(lats)) if lats else 0
        v["p95_latency_ms"] = _p95(lats)
        purposes_out.append(v)
    purposes_out.sort(key=lambda x: x["calls"], reverse=True)

    return {
        "period": period,
        "period_days": days,
        "total_calls": total_calls,
        "total_errors": total_errors,
        "total_prompt_tokens": total_tokens_prompt,
        "total_completion_tokens": total_tokens_completion,
        "total_tokens": total_tokens_prompt + total_tokens_completion,
        "avg_latency_ms": int(sum(all_latencies) / len(all_latencies)) if all_latencies else 0,
        "p95_latency_ms": _p95(all_latencies),
        "by_purpose": purposes_out,
        "timeseries": [by_day[k] for k in sorted(by_day.keys())],
    }


# ----------------------------------------------------------------------
# Coverage / health для модулей и индекса
# ----------------------------------------------------------------------


@router.get("/coverage")
async def coverage(session: SessionDep) -> dict[str, Any]:
    """Сколько тикетов / KB / суммаризаций в каждом модуле — для health-страницы."""
    tickets_by_module = (
        await session.execute(
            select(TicketORM.module, func.count())
            .group_by(TicketORM.module)
            .order_by(func.count().desc())
        )
    ).all()
    summaries_by_module = (
        await session.execute(
            select(TicketSummaryORM.affected_module, func.count())
            .group_by(TicketSummaryORM.affected_module)
        )
    ).all()
    from db.models import KBArticle

    kb_by_module = (
        await session.execute(
            select(KBArticle.module, func.count())
            .group_by(KBArticle.module)
        )
    ).all()
    kb_deprecated = int(
        (
            await session.execute(
                select(func.count()).select_from(KBArticle).where(KBArticle.is_deprecated.is_(True))
            )
        ).scalar()
        or 0
    )

    s_dict = {m: int(c) for m, c in summaries_by_module}
    k_dict = {m: int(c) for m, c in kb_by_module}
    modules: list[dict[str, Any]] = []
    seen = set()
    for m, c in tickets_by_module:
        seen.add(m or "")
        modules.append(
            {
                "module": m or "—",
                "tickets": int(c),
                "summaries": s_dict.get(m, 0),
                "kb_articles": k_dict.get(m, 0),
            }
        )
    # модули, которые есть в KB, но нет в тикетах — тоже показываем
    for m, c in kb_by_module:
        if (m or "") not in seen:
            modules.append(
                {
                    "module": m or "—",
                    "tickets": 0,
                    "summaries": 0,
                    "kb_articles": int(c),
                }
            )

    tickets_total = int(
        (await session.execute(select(func.count()).select_from(TicketORM))).scalar() or 0
    )
    summaries_total = int(
        (await session.execute(select(func.count()).select_from(TicketSummaryORM))).scalar() or 0
    )
    kb_total = int(
        (await session.execute(select(func.count()).select_from(KBArticle))).scalar() or 0
    )

    return {
        "tickets_total": tickets_total,
        "summaries_total": summaries_total,
        "summaries_coverage_pct": round(100 * summaries_total / tickets_total, 1) if tickets_total else 0,
        "kb_total": kb_total,
        "kb_deprecated": kb_deprecated,
        "modules": modules,
    }


# ----------------------------------------------------------------------
# Health: пинги адаптеров
# ----------------------------------------------------------------------


@router.get("/health-details")
async def health_details() -> dict[str, Any]:
    """Расширенный health-чек: пинги до LLM, embeddings, vector_store, FTS."""
    from api.dependencies import (
        embeddings_client as _emb_dep,
    )
    from api.dependencies import (
        llm_client as _llm_dep,
    )
    from api.dependencies import (
        text_search_client as _ts_dep,
    )
    from api.dependencies import (
        vector_store_client as _vs_dep,
    )

    async def _check(name: str, fn):  # type: ignore[no-untyped-def]
        import time as _t

        t0 = _t.time()
        try:
            await fn()
            return {"name": name, "status": "ok", "latency_ms": int((_t.time() - t0) * 1000)}
        except Exception as e:
            return {"name": name, "status": "error", "error": str(e)[:200]}

    llm = _llm_dep()
    emb = _emb_dep()
    vec = _vs_dep()
    fts = _ts_dep()

    from adapters.llm.base import ChatMessage

    async def _llm_ping():
        await llm.chat_completion(
            messages=[ChatMessage(role="user", content="ping")],
            temperature=0.0,
            max_tokens=1,
        )

    checks = [
        await _check("llm", _llm_ping),
        await _check("embeddings", lambda: emb.embed_query("ping")),
        await _check("vector_store", lambda: vec.health()),
        await _check("text_search", lambda: fts.count()),
    ]
    overall = "ok" if all(c["status"] == "ok" for c in checks) else "degraded"
    return {"status": overall, "checks": checks}


@router.get("/dashboard")
async def dashboard(
    session: SessionDep,
    period: Literal["day", "week", "month"] = Query(default="week"),
) -> dict[str, Any]:
    days = _period_days(period)
    now = _now()
    from_dt = now - timedelta(days=days)
    prev_from = now - timedelta(days=days * 2)

    # ---- общие счётчики (за всё время) ----
    total_tickets = int(
        (await session.execute(select(func.count()).select_from(TicketORM))).scalar() or 0
    )
    indexed = int(
        (
            await session.execute(select(func.count()).select_from(TicketSummaryORM))
        ).scalar()
        or 0
    )
    llm_calls = int(
        (await session.execute(select(func.count()).select_from(LLMCallLog))).scalar()
        or 0
    )

    # ---- LLM latency (за период) ----
    latencies = (
        await session.execute(
            select(LLMCallLog.latency_ms).where(LLMCallLog.created_at >= from_dt)
        )
    ).scalars().all()
    latencies_f = [float(x) for x in latencies]
    avg_lat = int(sum(latencies_f) / len(latencies_f)) if latencies_f else 0
    p95_lat = _p95(latencies_f)

    # ---- Распределения ----
    by_module = (
        await session.execute(
            select(TicketORM.module, func.count())
            .group_by(TicketORM.module)
            .order_by(func.count().desc())
        )
    ).all()
    by_status = (
        await session.execute(
            select(TicketORM.status, func.count()).group_by(TicketORM.status)
        )
    ).all()

    # ---- Timeseries: тикеты по дням за период ----
    tickets_in_period = (
        await session.execute(
            select(TicketORM.created_at, TicketORM.module).where(
                TicketORM.created_at >= from_dt
            )
        )
    ).all()
    days_buckets: dict[str, int] = {}
    # Заполняем все дни (включая пустые), чтобы график был ровный
    for d in range(days):
        key = _date_key(from_dt + timedelta(days=d))
        days_buckets[key] = 0
    for created_at, _ in tickets_in_period:
        key = _date_key(created_at)
        if key in days_buckets:
            days_buckets[key] += 1
    timeseries = [{"date": k, "count": v} for k, v in sorted(days_buckets.items())]

    # ---- Anomalies: модули с ростом ≥30% относительно предыдущего такого же
    # периода ----
    prev_tickets = (
        await session.execute(
            select(TicketORM.module).where(
                TicketORM.created_at >= prev_from, TicketORM.created_at < from_dt
            )
        )
    ).scalars().all()
    cur_module_counts = Counter(
        m for _, m in tickets_in_period if m
    )
    prev_module_counts = Counter(m for m in prev_tickets if m)

    anomalies: list[dict[str, Any]] = []
    for module, cur in cur_module_counts.items():
        prev = prev_module_counts.get(module, 0)
        # Считаем относительный рост; чтобы не делить на 0, минимум 1.
        denom = max(1, prev)
        delta_pct = (cur - prev) / denom
        if delta_pct >= 0.3 and cur >= 3:
            anomalies.append(
                {
                    "module": module,
                    "current": cur,
                    "previous": prev,
                    "delta_pct": round(delta_pct * 100, 1),
                }
            )
    anomalies.sort(key=lambda x: x["delta_pct"], reverse=True)

    # ---- Последний ингест ----
    last_job = (
        await session.execute(
            select(IngestJob).order_by(IngestJob.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    return {
        "period": period,
        "period_days": days,
        "tickets_total": total_tickets,
        "tickets_indexed_total": indexed,
        "tickets_in_period": len(tickets_in_period),
        "llm_calls_total": llm_calls,
        "llm_calls_in_period": len(latencies_f),
        "avg_llm_latency_ms": avg_lat,
        "p95_llm_latency_ms": p95_lat,
        "tickets_by_module": [
            {"module": m or "—", "count": int(c)} for m, c in by_module
        ],
        "tickets_by_status": [
            {"status": s, "count": int(c)} for s, c in by_status
        ],
        "timeseries": timeseries,
        "anomalies": anomalies,
        "last_ingest": (
            {
                "id": last_job.id,
                "status": last_job.status,
                "processed": last_job.processed_items,
                "failed": last_job.failed_items,
                "created_at": last_job.created_at.isoformat(),
            }
            if last_job
            else None
        ),
    }
