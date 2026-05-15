"""Поиск дубликата по эмбеддингу выжимки.

Если в индексе есть запись ``target_type='ticket_summary'`` со score выше
порога — возвращаем её ``target_id`` (это станет ``is_duplicate_of`` для
текущей выжимки).
"""

from __future__ import annotations

from adapters.vector_store.base import VectorStore
from config.logging import get_logger

logger = get_logger("pipelines.ticket_ingestion.deduplicate")


async def find_duplicate_canonical(
    summary_vector: list[float],
    vector_store: VectorStore,
    *,
    threshold: float = 0.92,
) -> str | None:
    try:
        hits = await vector_store.search(
            query_vector=summary_vector,
            top_k=3,
            target_types=["ticket_summary"],
            min_score=threshold,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("dedupe.search_failed", error=str(e))
        return None
    if not hits:
        return None
    return hits[0].target_id
