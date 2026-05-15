"""Интеграционные тесты ``ConversationsRepository``."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.conversations import ConversationsRepository


@pytest.mark.integration
async def test_create_and_add_messages(session: AsyncSession) -> None:
    repo = ConversationsRepository(session)
    conv = await repo.create(user_id="alice", title="Help with PDF")
    await session.commit()

    await repo.add_message(conversation_id=conv.id, role="user", content="Hello")
    await repo.add_message(
        conversation_id=conv.id,
        role="assistant",
        content="Hi! [1]",
        citations=[{"source_index": 1, "source_id": "kb-1"}],
        used_sources=[{"id": "kb-1", "title": "Loading PDF"}],
    )
    await session.commit()

    full = await repo.get(conv.id, with_messages=True)
    assert full is not None
    assert len(full.messages) == 2
    assert full.messages[0].role == "user"
    assert full.messages[1].citations_json == [{"source_index": 1, "source_id": "kb-1"}]


@pytest.mark.integration
async def test_list_by_user_orders_by_updated_at(session: AsyncSession) -> None:
    repo = ConversationsRepository(session)
    c1 = await repo.create(user_id="bob", title="First")
    c2 = await repo.create(user_id="bob", title="Second")
    await session.commit()

    # «Поднимаем» c1 наверх свежим сообщением.
    await asyncio.sleep(0.01)
    await repo.add_message(conversation_id=c1.id, role="user", content="bump")
    await session.commit()

    items = await repo.list_by_user("bob")
    assert [c.id for c in items[:2]] == [c1.id, c2.id]


@pytest.mark.integration
async def test_set_feedback(session: AsyncSession) -> None:
    repo = ConversationsRepository(session)
    conv = await repo.create(user_id="carol")
    msg = await repo.add_message(conversation_id=conv.id, role="assistant", content="ok")
    await session.commit()

    ok = await repo.set_feedback(msg.id, feedback=1, comment="helpful")
    await session.commit()
    assert ok is True

    await session.refresh(msg)
    assert msg.feedback == 1
    assert msg.feedback_comment == "helpful"

    assert await repo.set_feedback("missing-id", feedback=-1) is False
