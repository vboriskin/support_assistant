"""CLI переиндексации: проходит по всем тикетам с выжимкой и пересобирает
векторный + FTS-индексы. Нужен после смены модели эмбеддингов или промпта
суммаризации.

    python -m scripts.reindex                     # все индексированные тикеты
    python -m scripts.reindex --batch-size 50
    python -m scripts.reindex --target-type ticket_summary
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from adapters.embeddings.factory import create_embeddings_client
from adapters.text_search.base import TextSearchRecord
from adapters.text_search.factory import create_text_search
from adapters.vector_store.base import VectorRecord
from adapters.vector_store.factory import create_vector_store
from config.logging import configure_logging, get_logger
from config.settings import get_settings
from db.engine import dispose_engine, get_engine, get_session_factory
from db.models import Ticket as TicketORM
from db.models import TicketSummary as TicketSummaryORM


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _summary_text(s: TicketSummaryORM) -> str:
    parts = [s.summary_one_line, f"Симптом: {s.symptom}"]
    if s.solution_steps_json:
        parts.append("Решение: " + "; ".join(s.solution_steps_json))
    return ". ".join(parts)


async def _run(batch_size: int) -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("scripts.reindex")

    engine = get_engine(settings)
    sf = get_session_factory(settings)
    embeddings = create_embeddings_client(settings)
    vec = create_vector_store(settings, engine)
    fts = create_text_search(settings, engine)

    # Прогрев схем
    try:
        await vec.count()
        await fts.count()
    except Exception as e:  # noqa: BLE001
        log.warning("warmup_failed", error=str(e))

    total = 0
    failed = 0
    async with sf() as session:
        stmt = (
            select(TicketORM, TicketSummaryORM)
            .join(TicketSummaryORM, TicketSummaryORM.ticket_id == TicketORM.id)
        )
        rows = (await session.execute(stmt)).all()
        log.info("reindex.start", candidates=len(rows))

        # Батчами: эмбеддинг сразу пачкой, потом upsert.
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]
            summary_texts = [_summary_text(s) for _, s in batch]
            symptom_texts = [f"passage: {s.symptom}" for _, s in batch]
            try:
                sum_vecs = await embeddings.embed_documents(summary_texts)
                sym_vecs = await embeddings.embed_documents(symptom_texts)
            except Exception as e:  # noqa: BLE001
                log.warning("embed_failed", batch_start=batch_start, error=str(e))
                failed += len(batch)
                continue

            vec_records: list[VectorRecord] = []
            fts_records: list[TextSearchRecord] = []
            for (t, s), st, syt, sv, syv in zip(
                batch, summary_texts, symptom_texts, sum_vecs, sym_vecs, strict=True
            ):
                metadata = {
                    "module": s.affected_module or "",
                    "is_known_issue": s.is_known_issue,
                    "resolution_status": s.resolution_status,
                    "created_at": t.created_at.isoformat(),
                }
                vec_records.extend(
                    [
                        VectorRecord(
                            id=f"ts:{t.id}",
                            target_type="ticket_summary",
                            target_id=t.id,
                            text=st,
                            metadata=metadata,
                            vector=sv,
                        ),
                        VectorRecord(
                            id=f"sm:{t.id}",
                            target_type="ticket_symptom",
                            target_id=t.id,
                            text=syt,
                            metadata=metadata,
                            vector=syv,
                        ),
                    ]
                )
                content_parts = [s.symptom, t.description]
                if s.solution_steps_json:
                    content_parts.extend(s.solution_steps_json)
                fts_records.append(
                    TextSearchRecord(
                        id=f"ts:{t.id}",
                        target_type="ticket_summary",
                        target_id=t.id,
                        title=s.summary_one_line,
                        content="\n".join(content_parts),
                    )
                )
            try:
                await vec.upsert(vec_records)
            except Exception as e:  # noqa: BLE001
                log.warning("vec_upsert_failed", error=str(e))
            try:
                await fts.upsert(fts_records)
            except Exception as e:  # noqa: BLE001
                log.warning("fts_upsert_failed", error=str(e))
            total += len(batch)
            log.info("reindex.batch", done=total, total_candidates=len(rows))

        # Отметка indexed_at у всех
        from sqlalchemy import update as sa_update

        await session.execute(
            sa_update(TicketORM)
            .where(TicketORM.id.in_([t.id for t, _ in rows]))
            .values(indexed_at=_now())
        )
        await session.commit()

    try:
        await embeddings.aclose()
    finally:
        await dispose_engine()

    log.info("reindex.done", reindexed=total, failed=failed)
    print(f"reindexed: {total}, failed: {failed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reindex all tickets with summary")
    p.add_argument("--batch-size", type=int, default=64)
    args = p.parse_args(argv)
    return asyncio.run(_run(args.batch_size))


if __name__ == "__main__":
    sys.exit(main())
