"""GET /api/stale/kb — KB-статьи, требующие ревизии.

Критерий: не обновлялась N месяцев И на её чанки приходились отрицательные
feedback-оценки (или вообще не было feedback при наличии запросов).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from api.dependencies import SessionDep
from db.models import KBArticle, Message

router = APIRouter(prefix="/stale", tags=["stale"])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@router.get("/kb")
async def stale_kb(
    session: SessionDep,
    months: int = 6,
    only_with_negative_feedback: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cutoff = _now() - timedelta(days=30 * months)

    arts = (
        await session.execute(
            select(KBArticle)
            .where(KBArticle.updated_at < cutoff, KBArticle.is_deprecated.is_(False))
            .order_by(KBArticle.updated_at.asc())
            .limit(limit * 3)
        )
    ).scalars().all()

    if not arts:
        return []

    # Считаем feedback для каждой статьи: пробегаем по последним 1000
    # сообщениям и смотрим, какие used_sources на неё ссылаются.
    msgs = (
        await session.execute(
            select(Message)
            .where(Message.role == "assistant", Message.used_sources_json.isnot(None))
            .order_by(Message.created_at.desc())
            .limit(2000)
        )
    ).scalars().all()

    feedback_for: dict[str, dict[str, int]] = {a.id: {"pos": 0, "neg": 0, "refs": 0} for a in arts}
    for m in msgs:
        for s in m.used_sources_json or []:
            sid = s.get("source_id") or ""
            # source_id для kb_chunk выглядит как "<article_id>:<chunk_order>"
            article_id = sid.split(":")[0]
            if article_id in feedback_for:
                feedback_for[article_id]["refs"] += 1
                if m.feedback == 1:
                    feedback_for[article_id]["pos"] += 1
                elif m.feedback == -1:
                    feedback_for[article_id]["neg"] += 1

    out: list[dict[str, Any]] = []
    for art in arts:
        fb = feedback_for[art.id]
        if only_with_negative_feedback and fb["neg"] == 0:
            continue
        out.append(
            {
                "id": art.id,
                "title": art.title,
                "module": art.module,
                "updated_at": art.updated_at.isoformat(),
                "days_since_update": (_now() - art.updated_at).days,
                "refs_in_recent_answers": fb["refs"],
                "positive_feedback": fb["pos"],
                "negative_feedback": fb["neg"],
            }
        )
        if len(out) >= limit:
            break
    return out
