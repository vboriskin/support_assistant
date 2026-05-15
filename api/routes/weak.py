"""GET /api/weak — слабые ответы ассистента.

Считаем «слабыми» одно из трёх:
  - оператор поставил отрицательный feedback,
  - retrieval не нашёл ни одного источника (used_sources пуст),
  - в тексте ответа есть маркер «нет в источниках» (детект из ответа).

Возвращаем минимум полей для витрины: query, snippet ответа, conversation_id,
message_id, кол-во источников, feedback, дата. Группируем рядом сообщения
с одинаковым по нормализации текстом запроса — чтобы было видно повторы.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from api.dependencies import SessionDep
from db.models import Conversation, Message

router = APIRouter(prefix="/weak", tags=["weak"])


_NO_SOURCE_MARKERS = (
    "В базе знаний и истории закрытых тикетов нет информации",
    "источников не найдено",
)

_PERIOD = {"day": 1, "week": 7, "month": 30, "all": 3650}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _why(msg: Message) -> list[str]:
    reasons: list[str] = []
    if msg.feedback == -1:
        reasons.append("feedback_negative")
    if not msg.used_sources_json:
        reasons.append("no_sources")
    text = (msg.content or "")[:300]
    if any(marker in text for marker in _NO_SOURCE_MARKERS):
        reasons.append("declined")
    return reasons


@router.get("")
async def list_weak(
    session: SessionDep,
    period: Literal["day", "week", "month", "all"] = Query(default="month"),
    reason: Literal["all", "feedback_negative", "no_sources", "declined"] = "all",
    limit: int = 200,
) -> list[dict[str, Any]]:
    days = _PERIOD[period]
    from_dt = _now() - timedelta(days=days)

    msgs = (
        await session.execute(
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.role == "assistant", Message.created_at >= from_dt)
            .order_by(desc(Message.created_at))
            .limit(limit * 3)  # пост-фильтруем
        )
    ).all()

    # Парный user-запрос для каждого assistant-сообщения — берём ближайший
    # предыдущий user в той же конверсации (одно событие = два сообщения).
    out: list[dict[str, Any]] = []
    for msg, conv in msgs:
        reasons = _why(msg)
        if not reasons:
            continue
        if reason != "all" and reason not in reasons:
            continue
        # подтягиваем user-запрос
        user_stmt = (
            select(Message)
            .where(
                Message.conversation_id == conv.id,
                Message.role == "user",
                Message.created_at <= msg.created_at,
            )
            .order_by(desc(Message.created_at))
            .limit(1)
        )
        user_msg = (await session.execute(user_stmt)).scalar_one_or_none()
        out.append(
            {
                "conversation_id": conv.id,
                "message_id": msg.id,
                "user_query": user_msg.content if user_msg else "",
                "answer_snippet": (msg.content or "")[:240],
                "feedback": msg.feedback,
                "feedback_comment": msg.feedback_comment,
                "used_sources_count": len(msg.used_sources_json or []),
                "reasons": reasons,
                "created_at": msg.created_at.isoformat(),
                "title": conv.title,
                "ticket_id": conv.ticket_id,
            }
        )
        if len(out) >= limit:
            break
    return out
