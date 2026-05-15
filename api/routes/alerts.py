"""Алёрты: текущий статус + ручной trigger.

Background-watcher живёт в lifespan'е приложения (см. api/main.py). Этот
роут даёт UI: посмотреть текущие пороги, прочитать последние оценки,
дёрнуть проверку вручную (для тестирования вебхука).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from api.dependencies import SessionDep, settings_dep
from config.logging import get_logger
from config.settings import Settings
from db.models import LLMCallLog, Message

logger = get_logger("api.alerts")
router = APIRouter(prefix="/alerts", tags=["alerts"])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _p95(values: list[float]) -> int:
    if not values:
        return 0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(0.95 * len(s)) - 1))
    return int(s[k])


async def compute_signals(session, window_minutes: int = 60) -> dict[str, Any]:
    """Считаем актуальные сигналы за окно. Используется и хендлером, и
    фоновым watcher'ом."""
    from_dt = _now() - timedelta(minutes=window_minutes)

    rows = (
        await session.execute(
            select(LLMCallLog.latency_ms, LLMCallLog.error).where(
                LLMCallLog.created_at >= from_dt
            )
        )
    ).all()
    latencies = [float(l or 0) for l, _ in rows]
    error_count = sum(1 for _, e in rows if e)

    msgs = (
        await session.execute(
            select(Message.used_sources_json).where(
                Message.role == "assistant", Message.created_at >= from_dt
            )
        )
    ).scalars().all()
    total = len(msgs)
    no_src = sum(1 for s in msgs if not s)
    no_sources_ratio = (no_src / total) if total else 0.0

    return {
        "window_minutes": window_minutes,
        "llm_calls": len(latencies),
        "p95_latency_ms": _p95(latencies),
        "error_count": error_count,
        "assistant_messages": total,
        "no_sources_ratio": round(no_sources_ratio, 3),
    }


def _violations(signals: dict[str, Any], settings: Settings) -> list[str]:
    out: list[str] = []
    a = settings.alerts
    if signals["p95_latency_ms"] > a.p95_latency_threshold_ms:
        out.append(
            f"p95 LLM latency = {signals['p95_latency_ms']}ms > {a.p95_latency_threshold_ms}ms"
        )
    if signals["no_sources_ratio"] > a.no_sources_ratio_threshold:
        out.append(
            f"no_sources_ratio = {signals['no_sources_ratio']*100:.1f}%"
            f" > {a.no_sources_ratio_threshold*100:.1f}%"
        )
    if signals["error_count"] > a.error_count_threshold:
        out.append(
            f"LLM errors = {signals['error_count']} > {a.error_count_threshold}"
        )
    return out


class AlertSettingsUpdate(BaseModel):
    enabled: bool | None = None
    webhook_url: str | None = None
    p95_latency_threshold_ms: int | None = None
    no_sources_ratio_threshold: float | None = None
    error_count_threshold: int | None = None
    check_interval_sec: int | None = None


@router.get("/status")
async def status(
    session: SessionDep,
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, Any]:
    sig = await compute_signals(session, window_minutes=60)
    return {
        "settings": {
            "enabled": settings.alerts.enabled,
            "webhook_url_set": bool(settings.alerts.webhook_url),
            "p95_latency_threshold_ms": settings.alerts.p95_latency_threshold_ms,
            "no_sources_ratio_threshold": settings.alerts.no_sources_ratio_threshold,
            "error_count_threshold": settings.alerts.error_count_threshold,
            "check_interval_sec": settings.alerts.check_interval_sec,
        },
        "signals": sig,
        "violations": _violations(sig, settings),
    }


@router.post("/trigger")
async def trigger(
    session: SessionDep,
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, Any]:
    """Принудительный прогон: посчитать сигналы и (если есть violations и
    webhook) отправить уведомление. Используется UI-кнопкой «Проверить сейчас»."""
    sig = await compute_signals(session, window_minutes=60)
    violations = _violations(sig, settings)
    sent = False
    if violations and settings.alerts.webhook_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                payload = {
                    "text": "Support Assistant alerts:\n• " + "\n• ".join(violations),
                    "signals": sig,
                }
                await client.post(settings.alerts.webhook_url, json=payload)
                sent = True
        except Exception as e:  # noqa: BLE001
            logger.warning("alerts.webhook_failed", error=str(e))
    return {"signals": sig, "violations": violations, "webhook_sent": sent}
