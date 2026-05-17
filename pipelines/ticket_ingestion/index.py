"""Финальный шаг: запись тикета + выжимки в БД и индексы.

Важная архитектурная деталь: vector_store и text_search **не пишутся внутри
открытой ORM-транзакции**. ``SQLiteFTS5.upsert`` и ``SQLiteVecStore.upsert``
открывают собственное ``engine.begin()`` на тот же файл. SQLite в WAL не
допускает двух конкурентных пишущих транзакций — получим ``database is locked``,
ORM-транзакция откатится, а внешние индексы останутся с «осиротевшими»
записями.

Поэтому:

1. **Транзакция 1.** ``save_with_summary`` — INSERT тикета и summary в БД, commit.
2. ``vector_store.upsert`` + ``text_search.upsert`` — вне ORM-транзакций.
3. **Транзакция 2.** ``mark_indexed`` — отдельный апдейт ``indexed_at``.

Семантика: если шаг 2 упадёт — тикет в БД есть, но без ``indexed_at``.
Следующий прогон пропустит его по ``exists_by_external_id``; переиндексация —
через отдельный endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.text_search.base import TextSearch, TextSearchRecord
from adapters.vector_store.base import VectorRecord, VectorStore
from config.logging import get_logger
from core.models import Ticket, TicketSummary
from db.repositories.tickets import TicketsRepository

logger = get_logger("pipelines.ticket_ingestion.index")


def _summary_text(summary: TicketSummary) -> str:
    parts = [summary.summary_one_line, f"Симптом: {summary.symptom}"]
    if summary.solution_steps:
        parts.append("Решение: " + "; ".join(summary.solution_steps))
    return ". ".join(parts)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def index_ticket(
    *,
    ticket: Ticket,
    summary: TicketSummary,
    summary_vector: list[float],
    symptom_vector: list[float],
    session_factory: async_sessionmaker[AsyncSession],
    vector_store: VectorStore,
    text_search: TextSearch,
    pii_audit: dict[str, int] | None = None,
    masked_at: datetime | None = None,
) -> tuple[str, str]:
    """Возвращает ``(ticket_id, summary_id)``."""
    # 1. DB-транзакция: тикет + summary
    async with session_factory() as session, session.begin():
        repo = TicketsRepository(session)
        orm_t, orm_s = await repo.save_with_summary(
            ticket, summary, pii_audit=pii_audit, masked_at=masked_at
        )
        ticket_id = orm_t.id
        summary_id = orm_s.id

    # 2. Внешние индексы — после commit, без открытой ORM-транзакции
    summary_text = _summary_text(summary)
    symptom_text = f"passage: {summary.symptom}"
    metadata = {
        "module": summary.affected_module or "",
        "is_known_issue": summary.is_known_issue,
        "resolution_status": summary.resolution_status,
        "created_at": ticket.created_at.isoformat(),
    }
    # Падение vector/FTS-индекса не должно ронять весь тикет: DB-запись уже
    # закоммичена. При недоступности vector_store retrieval отработает через
    # FTS и наоборот. Полный re-index — через отдельный endpoint.
    try:
        await vector_store.upsert(
            [
                VectorRecord(
                    id=f"ts:{ticket_id}",
                    target_type="ticket_summary",
                    target_id=ticket_id,
                    text=summary_text,
                    metadata=metadata,
                    vector=summary_vector,
                ),
                VectorRecord(
                    id=f"sm:{ticket_id}",
                    target_type="ticket_symptom",
                    target_id=ticket_id,
                    text=symptom_text,
                    metadata=metadata,
                    vector=symptom_vector,
                ),
            ]
        )
    except Exception as e:
        logger.warning("index.vector_store_upsert_failed", ticket_id=ticket_id, error=str(e))

    content_parts = [summary.symptom, ticket.description]
    if summary.solution_steps:
        content_parts.extend(summary.solution_steps)
    try:
        await text_search.upsert(
            [
                TextSearchRecord(
                    id=f"ts:{ticket_id}",
                    target_type="ticket_summary",
                    target_id=ticket_id,
                    title=summary.summary_one_line,
                    content="\n".join(content_parts),
                )
            ]
        )
    except Exception as e:
        logger.warning("index.text_search_upsert_failed", ticket_id=ticket_id, error=str(e))

    # 3. Отметка indexed_at — отдельной транзакцией
    async with session_factory() as session, session.begin():
        await TicketsRepository(session).mark_indexed(ticket_id, indexed_at=_now())

    return ticket_id, summary_id
