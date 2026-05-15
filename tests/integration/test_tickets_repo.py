"""Интеграционные тесты ``TicketsRepository``."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.tickets import TicketsRepository


@pytest.mark.integration
async def test_create_and_get_by_id(session: AsyncSession) -> None:
    repo = TicketsRepository(session)
    t = await repo.create(
        external_id="SM-1",
        channel="email",
        subject="Не загружается выписка",
        description="Ошибка при попытке загрузить PDF",
        status="resolved",
        created_at=datetime(2026, 1, 15, 10, 30),
    )
    await session.commit()

    fetched = await repo.get(t.id)
    assert fetched is not None
    assert fetched.external_id == "SM-1"
    assert fetched.subject == "Не загружается выписка"
    assert fetched.status == "resolved"
    assert fetched.is_pii_masked is False


@pytest.mark.integration
async def test_exists_and_get_by_external_id(session: AsyncSession) -> None:
    repo = TicketsRepository(session)
    assert await repo.exists_by_external_id("SM-42") is False

    await repo.create(
        external_id="SM-42",
        channel="sm",
        subject="x",
        description="y",
        status="open",
        created_at=datetime(2026, 2, 1),
    )
    await session.commit()

    assert await repo.exists_by_external_id("SM-42") is True
    t = await repo.get_by_external_id("SM-42")
    assert t is not None
    assert t.subject == "x"


@pytest.mark.integration
async def test_update_fields_and_helpers(session: AsyncSession) -> None:
    repo = TicketsRepository(session)
    t = await repo.create(
        external_id="SM-2",
        channel="email",
        subject="s",
        description="d",
        status="open",
        created_at=datetime(2026, 3, 1),
    )
    await session.commit()

    masked_at = datetime(2026, 3, 2, 12, 0)
    await repo.mark_pii_masked(t.id, masked_at=masked_at, audit={"EMAIL": 2, "PHONE": 1})
    indexed_at = datetime(2026, 3, 2, 13, 0)
    await repo.mark_indexed(t.id, indexed_at=indexed_at)
    await session.commit()

    # update_fields идёт SQL-statement-ом мимо ORM — кэш identity map нужно
    # явно обновить, иначе атрибуты на ``t`` останутся прежними.
    await session.refresh(t)
    assert t.is_pii_masked is True
    assert t.masked_at == masked_at
    assert t.pii_audit_json == {"EMAIL": 2, "PHONE": 1}
    assert t.indexed_at == indexed_at


@pytest.mark.integration
async def test_delete_returns_rowcount(session: AsyncSession) -> None:
    repo = TicketsRepository(session)
    t = await repo.create(
        external_id="SM-3",
        channel="email",
        subject="s",
        description="d",
        status="open",
        created_at=datetime(2026, 4, 1),
    )
    await session.commit()

    removed = await repo.delete(t.id)
    assert removed == 1
    await session.commit()
    assert await repo.get(t.id) is None
    assert await repo.delete("does-not-exist") == 0


@pytest.mark.integration
async def test_external_id_unique(session: AsyncSession) -> None:
    from sqlalchemy.exc import IntegrityError

    repo = TicketsRepository(session)
    await repo.create(
        external_id="SM-99",
        channel="sm",
        subject="s",
        description="d",
        status="open",
        created_at=datetime(2026, 1, 1),
    )
    await session.commit()
    # Второй create на тот же external_id падает уже на flush() внутри repo —
    # SQLite поднимает UNIQUE-нарушение немедленно.
    with pytest.raises(IntegrityError):
        await repo.create(
            external_id="SM-99",
            channel="sm",
            subject="s2",
            description="d2",
            status="open",
            created_at=datetime(2026, 1, 2),
        )
