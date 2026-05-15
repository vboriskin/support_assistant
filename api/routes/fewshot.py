"""CRUD few-shot примеров: pending → approved → используется в промпте."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.dependencies import SessionDep, get_user_id
from db.models import FewShotExample, Message

router = APIRouter(prefix="/fewshot", tags=["fewshot"])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _to_dict(e: FewShotExample) -> dict[str, Any]:
    return {
        "id": e.id,
        "set_name": e.set_name,
        "user_text": e.user_text,
        "assistant_text": e.assistant_text,
        "status": e.status,
        "source_message_id": e.source_message_id,
        "note": e.note,
        "created_by": e.created_by,
        "created_at": e.created_at.isoformat(),
        "reviewed_by": e.reviewed_by,
        "reviewed_at": e.reviewed_at.isoformat() if e.reviewed_at else None,
    }


class FewShotCreate(BaseModel):
    set_name: str = Field(default="assistant", max_length=80)
    user_text: str = Field(..., min_length=1, max_length=8000)
    assistant_text: str = Field(..., min_length=1, max_length=20000)
    source_message_id: str | None = None
    note: str | None = Field(default=None, max_length=1000)


class FewShotReview(BaseModel):
    status: Literal["approved", "rejected", "pending"]
    note: str | None = Field(default=None, max_length=1000)


@router.get("")
async def list_examples(
    session: SessionDep,
    status: Literal["all", "pending", "approved", "rejected"] = "all",
    set_name: str = "assistant",
    limit: int = 200,
) -> list[dict[str, Any]]:
    stmt = (
        select(FewShotExample)
        .where(FewShotExample.set_name == set_name)
        .order_by(FewShotExample.created_at.desc())
        .limit(limit)
    )
    if status != "all":
        stmt = stmt.where(FewShotExample.status == status)
    items = (await session.execute(stmt)).scalars().all()
    return [_to_dict(e) for e in items]


@router.post("")
async def create_example(
    body: FewShotCreate,
    session: SessionDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> dict[str, Any]:
    # Если передан source_message_id и текстов нет — попробуем подтянуть
    user_text = body.user_text
    asst_text = body.assistant_text
    if body.source_message_id and (not user_text or not asst_text):
        msg = await session.get(Message, body.source_message_id)
        if msg and msg.role == "assistant":
            asst_text = asst_text or msg.content
            # ближайший user-запрос той же беседы
            prev = (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == msg.conversation_id,
                        Message.role == "user",
                        Message.created_at <= msg.created_at,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if prev:
                user_text = user_text or prev.content
    if not user_text or not asst_text:
        raise HTTPException(422, "user_text and assistant_text are required")

    e = FewShotExample(
        id=str(uuid.uuid4()),
        set_name=body.set_name,
        user_text=user_text,
        assistant_text=asst_text,
        status="pending",
        source_message_id=body.source_message_id,
        note=body.note,
        created_by=user_id,
        created_at=_now(),
    )
    session.add(e)
    await session.commit()
    return _to_dict(e)


@router.post("/{example_id}/review")
async def review_example(
    example_id: str,
    body: FewShotReview,
    session: SessionDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> dict[str, Any]:
    e = await session.get(FewShotExample, example_id)
    if e is None:
        raise HTTPException(404, "example not found")
    e.status = body.status
    if body.note is not None:
        e.note = body.note
    e.reviewed_by = user_id
    e.reviewed_at = _now()
    await session.flush()
    await session.commit()
    return _to_dict(e)


@router.delete("/{example_id}")
async def delete_example(example_id: str, session: SessionDep) -> dict[str, str]:
    e = await session.get(FewShotExample, example_id)
    if e is None:
        raise HTTPException(404, "example not found")
    await session.delete(e)
    await session.commit()
    return {"status": "ok"}
