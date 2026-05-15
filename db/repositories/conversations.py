"""Репозиторий для ``conversations`` и ``messages``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Conversation, Message


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ConversationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: str,
        title: str | None = None,
        ticket_id: str | None = None,
        id: str | None = None,
    ) -> Conversation:
        now = _now()
        conv = Conversation(
            id=id or str(uuid.uuid4()),
            user_id=user_id,
            ticket_id=ticket_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self.session.add(conv)
        await self.session.flush()
        return conv

    async def get(self, id: str, *, with_messages: bool = False) -> Conversation | None:
        if with_messages:
            stmt = (
                select(Conversation)
                .where(Conversation.id == id)
                .options(selectinload(Conversation.messages))
            )
            return (await self.session.execute(stmt)).scalar_one_or_none()
        return await self.session.get(Conversation, id)

    async def list_by_user(self, user_id: str, *, limit: int = 50) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        used_sources: list[dict[str, Any]] | None = None,
        id: str | None = None,
    ) -> Message:
        now = _now()
        msg = Message(
            id=id or str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content,
            citations_json=citations,
            used_sources_json=used_sources,
            created_at=now,
        )
        self.session.add(msg)
        # Подвинем updated_at у разговора, чтобы list-by-user сортировался корректно.
        conv = await self.session.get(Conversation, conversation_id)
        if conv is not None:
            conv.updated_at = now
        await self.session.flush()
        return msg

    async def set_feedback(
        self,
        message_id: str,
        *,
        feedback: int,
        comment: str | None = None,
    ) -> bool:
        msg = await self.session.get(Message, message_id)
        if msg is None:
            return False
        msg.feedback = feedback
        if comment is not None:
            msg.feedback_comment = comment
        await self.session.flush()
        return True
