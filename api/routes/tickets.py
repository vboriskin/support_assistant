"""Tickets list / detail / reindex."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.dependencies import (
    SessionDep,
    embeddings_client,
    text_search_client,
    vector_store_client,
)
from db.models import Ticket as TicketORM
from db.models import TicketSummary as TicketSummaryORM


def _text_search_for_search(ts=Depends(text_search_client)):
    return ts

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _ticket_to_dict(t: TicketORM) -> dict[str, Any]:
    return {
        "id": t.id,
        "external_id": t.external_id,
        "channel": t.channel,
        "category": t.category,
        "module": t.module,
        "subject": t.subject,
        "status": t.status,
        "priority": t.priority,
        "tags": t.tags_json or [],
        "created_at": t.created_at.isoformat(),
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "indexed_at": t.indexed_at.isoformat() if t.indexed_at else None,
        "is_pii_masked": t.is_pii_masked,
    }


def _summary_to_dict(s: TicketSummaryORM) -> dict[str, Any]:
    return {
        "id": s.id,
        "ticket_id": s.ticket_id,
        "summary_one_line": s.summary_one_line,
        "symptom": s.symptom,
        "root_cause": s.root_cause,
        "solution_steps": s.solution_steps_json or [],
        "affected_module": s.affected_module,
        "user_role": s.user_role,
        "is_known_issue": s.is_known_issue,
        "resolution_status": s.resolution_status,
        "is_duplicate_of": s.is_duplicate_of,
        "model_used": s.model_used,
        "generated_at": s.generated_at.isoformat(),
    }


@router.get("")
async def list_tickets(
    session: SessionDep,
    q: str | None = None,
    module: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    text_search=Depends(_text_search_for_search),
) -> dict[str, Any]:
    from services.ticket_search import TicketSearchService

    svc = TicketSearchService(session=session, text_search=text_search)
    return await svc.search(
        q=q, module=module, status=status, page=page, page_size=page_size
    )


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    session: SessionDep,
) -> dict[str, Any]:
    stmt = (
        select(TicketORM)
        .where(TicketORM.id == ticket_id)
        .options(selectinload(TicketORM.summary))
    )
    t = (await session.execute(stmt)).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    out = _ticket_to_dict(t)
    out["description"] = t.description
    out["conversation"] = t.conversation_json or []
    out["pii_audit"] = t.pii_audit_json
    out["summary"] = _summary_to_dict(t.summary) if t.summary else None
    return out


@router.post("/{ticket_id}/reindex")
async def reindex_ticket(
    ticket_id: str,
    session: SessionDep,
    embeddings=Depends(embeddings_client),
    vector_store=Depends(vector_store_client),
    text_search=Depends(text_search_client),
) -> dict[str, Any]:
    """Пересобирает векторный + FTS индексы для конкретного тикета.

    Требует, чтобы тикет уже был в БД с summary. Если summary нет —
    возвращает 409 (для генерации summary нужен полный re-ingest или
    отдельный endpoint, которого пока нет).
    """
    from adapters.text_search.base import TextSearchRecord
    from adapters.vector_store.base import VectorRecord

    stmt = (
        select(TicketORM)
        .where(TicketORM.id == ticket_id)
        .options(selectinload(TicketORM.summary))
    )
    t = (await session.execute(stmt)).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "ticket not found")
    if t.summary is None:
        raise HTTPException(409, "ticket has no summary; full re-ingest required")
    s = t.summary

    summary_text = (
        f"{s.summary_one_line}. Симптом: {s.symptom}."
        + (" Решение: " + "; ".join(s.solution_steps_json) if s.solution_steps_json else "")
    )
    symptom_text = f"passage: {s.symptom}"

    # Удаляем старые записи (на случай смены эмбеддингов / содержимого)
    await vector_store.delete_by_target("ticket_summary", [t.id])
    await vector_store.delete_by_target("ticket_symptom", [t.id])
    await text_search.delete_by_target("ticket_summary", [t.id])

    vectors = await embeddings.embed_documents([summary_text, symptom_text])
    metadata = {
        "module": s.affected_module or "",
        "is_known_issue": s.is_known_issue,
        "resolution_status": s.resolution_status,
        "created_at": t.created_at.isoformat(),
    }
    await vector_store.upsert(
        [
            VectorRecord(
                id=f"ts:{t.id}",
                target_type="ticket_summary",
                target_id=t.id,
                text=summary_text,
                metadata=metadata,
                vector=vectors[0],
            ),
            VectorRecord(
                id=f"sm:{t.id}",
                target_type="ticket_symptom",
                target_id=t.id,
                text=symptom_text,
                metadata=metadata,
                vector=vectors[1],
            ),
        ]
    )
    content_parts = [s.symptom, t.description]
    if s.solution_steps_json:
        content_parts.extend(s.solution_steps_json)
    await text_search.upsert(
        [
            TextSearchRecord(
                id=f"ts:{t.id}",
                target_type="ticket_summary",
                target_id=t.id,
                title=s.summary_one_line,
                content="\n".join(content_parts),
            )
        ]
    )
    from datetime import UTC, datetime

    t.indexed_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    return {"status": "ok", "ticket_id": t.id, "indexed_at": t.indexed_at.isoformat()}
