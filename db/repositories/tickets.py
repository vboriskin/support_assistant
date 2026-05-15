"""Репозиторий для ``tickets``.

Скоуп — операции CRUD и проверки, нужные ингесту и API:

- создать тикет;
- найти по внутреннему / внешнему id;
- быстрая проверка ``exists_by_external_id`` (для идемпотентности повторного
  прогона CSV);
- пометить, что PII замаскирован и/или тикет проиндексирован;
- обновить произвольные поля.

Бизнес-логика (валидация переходов статусов и т.п.) сюда не лезет — это
ответственность сервисов на этапах 7+.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Ticket as DomainTicket
from core.models import TicketSummary as DomainTicketSummary
from db.models import Ticket, TicketSummary


class TicketsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        external_id: str,
        channel: str,
        subject: str,
        description: str,
        status: str,
        created_at: datetime,
        category: str | None = None,
        module: str | None = None,
        conversation: list[dict[str, Any]] | None = None,
        author_role: str | None = None,
        assignee: str | None = None,
        priority: str | None = None,
        tags: list[str] | None = None,
        closed_at: datetime | None = None,
        raw_fields: dict[str, Any] | None = None,
        id: str | None = None,
    ) -> Ticket:
        ticket = Ticket(
            id=id or str(uuid.uuid4()),
            external_id=external_id,
            channel=channel,
            subject=subject,
            description=description,
            status=status,
            created_at=created_at,
            category=category,
            module=module,
            conversation_json=conversation or [],
            author_role=author_role,
            assignee=assignee,
            priority=priority,
            tags_json=tags,
            closed_at=closed_at,
            raw_fields_json=raw_fields,
            is_pii_masked=False,
        )
        self.session.add(ticket)
        await self.session.flush()
        return ticket

    async def get(self, id: str) -> Ticket | None:
        return await self.session.get(Ticket, id)

    async def get_by_external_id(self, external_id: str) -> Ticket | None:
        stmt = select(Ticket).where(Ticket.external_id == external_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def exists_by_external_id(self, external_id: str) -> bool:
        stmt = select(exists().where(Ticket.external_id == external_id))
        return bool((await self.session.execute(stmt)).scalar())

    async def update_fields(self, id: str, **fields: Any) -> int:
        """Точечное обновление полей. Возвращает число изменённых строк."""
        if not fields:
            return 0
        stmt = update(Ticket).where(Ticket.id == id).values(**fields)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def mark_pii_masked(
        self,
        id: str,
        *,
        masked_at: datetime,
        audit: Mapping[str, int] | None = None,
    ) -> None:
        await self.update_fields(
            id,
            is_pii_masked=True,
            masked_at=masked_at,
            pii_audit_json=dict(audit) if audit else None,
        )

    async def mark_indexed(self, id: str, *, indexed_at: datetime) -> None:
        await self.update_fields(id, indexed_at=indexed_at)

    async def delete(self, id: str) -> int:
        stmt = delete(Ticket).where(Ticket.id == id)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def count(self) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(Ticket)
        return int((await self.session.execute(stmt)).scalar() or 0)

    # ------------------------------------------------------------------
    # Высокоуровневые операции для ingest-пайплайна
    # ------------------------------------------------------------------

    async def save_masked(
        self,
        ticket: DomainTicket,
        *,
        pii_audit: dict[str, int] | None = None,
        masked_at: datetime | None = None,
    ) -> Ticket:
        """Сохраняет тикет, уже прошедший PII-маскирование.

        Если ``ticket.id`` пустой — генерируется новый UUID. Помечает
        ``is_pii_masked=True``. Возвращает ORM-объект.
        """
        orm = Ticket(
            id=ticket.id or str(uuid.uuid4()),
            external_id=ticket.external_id,
            channel=ticket.channel,
            subject=ticket.subject,
            description=ticket.description,
            status=ticket.status,
            created_at=ticket.created_at,
            category=ticket.category,
            module=ticket.module,
            conversation_json=[c.model_dump(mode="json") for c in ticket.conversation],
            author_role=ticket.author_role,
            assignee=ticket.assignee,
            priority=ticket.priority,
            tags_json=ticket.tags or None,
            closed_at=ticket.closed_at,
            raw_fields_json=ticket.raw_fields or None,
            is_pii_masked=True,
            masked_at=masked_at,
            pii_audit_json=dict(pii_audit) if pii_audit else None,
        )
        self.session.add(orm)
        await self.session.flush()
        return orm

    async def save_with_summary(
        self,
        ticket: DomainTicket,
        summary: DomainTicketSummary,
        *,
        pii_audit: dict[str, int] | None = None,
        masked_at: datetime | None = None,
    ) -> tuple[Ticket, TicketSummary]:
        """Транзакционно сохраняет тикет + выжимку (1:1)."""
        if not ticket.id:
            ticket.id = str(uuid.uuid4())
        if not summary.ticket_id:
            summary.ticket_id = ticket.id
        if summary.ticket_id != ticket.id:
            raise ValueError("summary.ticket_id != ticket.id")

        orm_t = await self.save_masked(ticket, pii_audit=pii_audit, masked_at=masked_at)
        orm_s = TicketSummary(
            id=str(uuid.uuid4()),
            ticket_id=orm_t.id,
            summary_one_line=summary.summary_one_line,
            symptom=summary.symptom,
            root_cause=summary.root_cause,
            solution_steps_json=list(summary.solution_steps),
            affected_module=summary.affected_module,
            user_role=summary.user_role,
            is_known_issue=summary.is_known_issue,
            resolution_status=summary.resolution_status,
            is_duplicate_of=summary.is_duplicate_of,
            generated_at=summary.generated_at,
            model_used=summary.model_used,
        )
        self.session.add(orm_s)
        await self.session.flush()
        return orm_t, orm_s
