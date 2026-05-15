"""Репозиторий ``ingest_jobs`` — учёт фоновых ингест-задач.

Жизненный цикл задачи:

    pending → running → succeeded | failed | cancelled

API даёт пользователю id задачи, и UI опрашивает прогресс по ``/ingest/jobs/{id}``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import IngestJob

JobStatus = str  # pending | running | succeeded | failed | cancelled


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IngestJobsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        job_type: str,
        total_items: int | None = None,
        metadata: dict[str, Any] | None = None,
        id: str | None = None,
    ) -> IngestJob:
        job = IngestJob(
            id=id or str(uuid.uuid4()),
            job_type=job_type,
            status="pending",
            total_items=total_items,
            processed_items=0,
            failed_items=0,
            created_at=_now(),
            metadata_json=metadata,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, id: str) -> IngestJob | None:
        return await self.session.get(IngestJob, id)

    async def mark_running(self, id: str) -> None:
        job = await self.session.get(IngestJob, id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _now()
        await self.session.flush()

    async def update_progress(
        self,
        id: str,
        *,
        processed: int | None = None,
        failed: int | None = None,
    ) -> None:
        job = await self.session.get(IngestJob, id)
        if job is None:
            return
        if processed is not None:
            job.processed_items = processed
        if failed is not None:
            job.failed_items = failed
        await self.session.flush()

    async def mark_finished(
        self,
        id: str,
        *,
        status: JobStatus,
        error: str | None = None,
    ) -> None:
        if status not in ("succeeded", "failed", "cancelled"):
            raise ValueError(f"Invalid terminal status: {status}")
        job = await self.session.get(IngestJob, id)
        if job is None:
            return
        job.status = status
        job.error_message = error
        job.finished_at = _now()
        await self.session.flush()

    async def list_recent(self, *, limit: int = 50) -> list[IngestJob]:
        stmt = select(IngestJob).order_by(IngestJob.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())
