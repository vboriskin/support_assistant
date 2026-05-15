"""Интеграционные тесты ``IngestJobsRepository``."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.ingest_jobs import IngestJobsRepository


@pytest.mark.integration
async def test_lifecycle_pending_running_succeeded(session: AsyncSession) -> None:
    repo = IngestJobsRepository(session)
    job = await repo.create(job_type="tickets_csv", total_items=10)
    await session.commit()
    assert job.status == "pending"
    assert job.processed_items == 0

    await repo.mark_running(job.id)
    await session.commit()
    refreshed = await repo.get(job.id)
    assert refreshed is not None
    assert refreshed.status == "running"
    assert refreshed.started_at is not None

    await repo.update_progress(job.id, processed=5, failed=1)
    await session.commit()
    refreshed = await repo.get(job.id)
    assert refreshed is not None
    assert refreshed.processed_items == 5
    assert refreshed.failed_items == 1

    await repo.mark_finished(job.id, status="succeeded")
    await session.commit()
    refreshed = await repo.get(job.id)
    assert refreshed is not None
    assert refreshed.status == "succeeded"
    assert refreshed.finished_at is not None


@pytest.mark.integration
async def test_mark_finished_rejects_invalid_status(session: AsyncSession) -> None:
    repo = IngestJobsRepository(session)
    job = await repo.create(job_type="kb")
    await session.commit()
    with pytest.raises(ValueError):
        await repo.mark_finished(job.id, status="running")


@pytest.mark.integration
async def test_list_recent_orders_newest_first(session: AsyncSession) -> None:
    repo = IngestJobsRepository(session)
    a = await repo.create(job_type="tickets_csv")
    b = await repo.create(job_type="tickets_csv")
    await session.commit()

    items = await repo.list_recent()
    # последний созданный — первый в списке
    assert items[0].id == b.id
    assert items[1].id == a.id
