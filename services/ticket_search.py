"""Поиск тикетов с FTS-фильтром и пагинацией.

Если задан ``q`` — ищем через text_search (FTS5/tsvector), фильтруем по
``target_type='ticket_summary'``, потом дотягиваем тикеты из БД по
``target_id``. Без ``q`` — просто SQL-сортировка + фильтры.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.text_search.base import TextSearch
from db.models import Ticket as TicketORM


class TicketSearchService:
    def __init__(self, *, session: AsyncSession, text_search: TextSearch) -> None:
        self.session = session
        self.text_search = text_search

    async def search(
        self,
        *,
        q: str | None = None,
        module: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(page_size, 200))
        offset = (page - 1) * page_size

        if q and q.strip():
            # FTS-поиск даёт нам target_id (=ticket.id) ранжированно.
            hits = await self.text_search.search(
                q.strip(), top_k=page_size * 3, target_types=["ticket_summary"]
            )
            ids = [h.target_id for h in hits[: page_size]]
            if not ids:
                return {"items": [], "page": page, "page_size": page_size, "query": q}
            stmt = select(TicketORM).where(TicketORM.id.in_(ids))
            if module:
                stmt = stmt.where(TicketORM.module == module)
            if status:
                stmt = stmt.where(TicketORM.status == status)
            rows = list((await self.session.execute(stmt)).scalars().all())
            # Сохраняем порядок FTS-релевантности
            by_id = {t.id: t for t in rows}
            ordered = [by_id[i] for i in ids if i in by_id]
            return {
                "items": [self._row(t) for t in ordered],
                "page": page,
                "page_size": page_size,
                "query": q,
            }

        stmt = select(TicketORM).order_by(TicketORM.created_at.desc())
        if module:
            stmt = stmt.where(TicketORM.module == module)
        if status:
            stmt = stmt.where(TicketORM.status == status)
        stmt = stmt.offset(offset).limit(page_size)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return {
            "items": [self._row(t) for t in rows],
            "page": page,
            "page_size": page_size,
            "query": q,
        }

    @staticmethod
    def _row(t: TicketORM) -> dict[str, Any]:
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
