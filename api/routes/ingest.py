"""POST /api/ingest/csv + GET /api/ingest/jobs[/{id}] + SSE stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from api.dependencies import (
    _session_factory,
    get_user_id,
    ingest_jobs_repo,
    ingest_pipeline_dep,
)
from api.schemas import IngestJobStartedResponse
from config.logging import get_logger
from config.settings import get_settings
from core.security import safe_upload_path
from db.repositories.ingest_jobs import IngestJobsRepository
from pipelines.ticket_ingestion.pipeline import TicketIngestionPipeline

logger = get_logger("api.ingest")

router = APIRouter(prefix="/ingest", tags=["ingest"])

# ---------- In-memory pub/sub для SSE-прогресса ----------
_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_latest_progress: dict[str, dict[str, Any]] = {}


def _subscribe(job_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    _subscribers[job_id].add(q)
    return q


def _unsubscribe(job_id: str, q: asyncio.Queue) -> None:
    _subscribers[job_id].discard(q)
    if not _subscribers[job_id]:
        _subscribers.pop(job_id, None)


def _publish(job_id: str, payload: dict[str, Any]) -> None:
    _latest_progress[job_id] = payload
    for q in list(_subscribers.get(job_id, [])):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def _uploads_dir() -> Path:
    p = get_settings().db.sqlite_path.parent / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _run_csv_job(
    pipeline: TicketIngestionPipeline,
    job_id: str,
    path: str,
) -> None:
    factory = _session_factory()
    try:
        async with factory() as session, session.begin():
            await IngestJobsRepository(session).mark_running(job_id)

        _publish(job_id, {"event": "running", "job_id": job_id, "stats": None})

        async def _progress(stats: dict[str, Any]) -> None:
            _publish(
                job_id,
                {
                    "event": "progress",
                    "job_id": job_id,
                    "stats": {
                        "total": stats.get("total", 0),
                        "indexed": stats.get("indexed", 0),
                        "skipped": stats.get("skipped", 0),
                        "failed": stats.get("failed", 0),
                        "saved_without_summary": stats.get("saved_without_summary", 0),
                    },
                },
            )

        stats = await pipeline.run(path, progress_callback=_progress)
        _publish(job_id, {"event": "done", "job_id": job_id, "stats": stats})

        async with factory() as session, session.begin():
            # total_items, processed, failed — в колонках; полная сводка
            # (skipped + распределение) — в metadata_json для UI.
            from sqlalchemy import update as sa_update

            from db.models import IngestJob

            await session.execute(
                sa_update(IngestJob)
                .where(IngestJob.id == job_id)
                .values(
                    total_items=int(stats.get("total", 0)),
                    processed_items=int(stats.get("processed", 0)),
                    failed_items=int(stats.get("failed", 0)),
                    metadata_json={
                        "skipped": int(stats.get("skipped", 0)),
                        "indexed": int(stats.get("indexed", 0)),
                        "saved_without_summary": int(stats.get("saved_without_summary", 0)),
                        "by_resolution": stats.get("by_resolution", {}),
                        "by_skip_reason": stats.get("by_skip_reason", {}),
                        "pii_audit_total": stats.get("pii_audit_total", {}),
                    },
                )
            )
        async with factory() as session, session.begin():
            await IngestJobsRepository(session).mark_finished(job_id, status="succeeded")
    except Exception as e:
        logger.exception("ingest.job_failed", job_id=job_id, error=str(e))
        try:
            async with factory() as session, session.begin():
                await IngestJobsRepository(session).mark_finished(
                    job_id, status="failed", error=str(e)
                )
        except Exception:
            pass


@router.post("/csv", response_model=IngestJobStartedResponse)
async def ingest_csv(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    _user_id: str = Depends(get_user_id),
    pipeline: TicketIngestionPipeline = Depends(ingest_pipeline_dep),
    jobs: IngestJobsRepository = Depends(ingest_jobs_repo),
) -> IngestJobStartedResponse:
    max_body = get_settings().security.max_body_bytes
    content = await file.read()
    if len(content) > max_body:
        raise HTTPException(status_code=413, detail="CSV too large")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    job_id = str(uuid.uuid4())
    target = safe_upload_path(file.filename or "tickets.csv", _uploads_dir())
    target.write_bytes(content)

    await jobs.create(
        id=job_id,
        job_type="tickets_csv",
        metadata={"filename": file.filename, "size": len(content), "stored_as": target.name},
    )
    await jobs.session.commit()

    background.add_task(_run_csv_job, pipeline, job_id, str(target))
    return IngestJobStartedResponse(job_id=job_id, status="started")


@router.get("/jobs")
async def list_jobs(
    limit: int = 50,
    jobs: IngestJobsRepository = Depends(ingest_jobs_repo),
) -> list[dict[str, Any]]:
    items = await jobs.list_recent(limit=limit)
    return [
        {
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "processed_items": j.processed_items,
            "failed_items": j.failed_items,
            "total_items": j.total_items,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "created_at": j.created_at.isoformat(),
            "error_message": j.error_message,
        }
        for j in items
    ]


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    jobs: IngestJobsRepository = Depends(ingest_jobs_repo),
) -> dict[str, Any]:
    j = await jobs.get(job_id)
    if j is None:
        raise HTTPException(404, detail="job not found")
    return {
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "processed_items": j.processed_items,
        "failed_items": j.failed_items,
        "total_items": j.total_items,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "created_at": j.created_at.isoformat(),
        "error_message": j.error_message,
        "metadata": j.metadata_json,
    }


@router.get("/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str) -> StreamingResponse:
    """SSE-стрим прогресса конкретного ingest-job'а.

    Сразу отдаёт последний снэпшот (если был), затем — все новые события до
    ``done``. Используется UI вместо polling.
    """
    queue = _subscribe(job_id)

    async def gen():
        try:
            snap = _latest_progress.get(job_id)
            if snap is not None:
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                except TimeoutError:
                    # keepalive
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if payload.get("event") == "done":
                    yield "data: [DONE]\n\n"
                    break
        finally:
            _unsubscribe(job_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
