"""Финальный шаг KB-ингеста: запись статьи + чанков в БД, индексация.

Тот же принцип, что и в ingest тикетов: внешние индексы (vector / text)
пишутся ПОСЛЕ commit'а ORM-транзакции, чтобы не словить
"database is locked" на SQLite.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.embeddings.base import EmbeddingsClient
from adapters.text_search.base import TextSearch, TextSearchRecord
from adapters.vector_store.base import VectorRecord, VectorStore
from config.logging import get_logger
from core.chunking import Chunk
from db.repositories.kb import KBRepository

logger = get_logger("pipelines.kb_ingestion.index")


async def index_article(
    *,
    title: str,
    body: str,
    chunks: list[Chunk],
    embeddings: EmbeddingsClient,
    vector_store: VectorStore,
    text_search: TextSearch,
    session_factory: async_sessionmaker[AsyncSession],
    module: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    source_path: str | None = None,
    audience: str = "internal",
) -> str:
    """Возвращает ``article_id``."""
    async with session_factory() as session, session.begin():
        repo = KBRepository(session)
        art = await repo.create_article(
            title=title,
            body=body,
            audience=audience,
            module=module,
            category=category,
            tags=tags,
            source_path=source_path,
        )
        await repo.replace_chunks(
            art.id,
            [
                {
                    "text": ch.text,
                    "section_title": ch.section_title,
                    "chunk_order": ch.chunk_order,
                }
                for ch in chunks
            ],
        )
        article_id = art.id

    if not chunks:
        return article_id

    # Эмбеддинги одним батчем
    texts = [c.text for c in chunks]
    try:
        vectors = await embeddings.embed_documents(texts)
    except Exception as e:  # noqa: BLE001
        logger.warning("kb.embed_failed", article_id=article_id, error=str(e))
        vectors = []

    if vectors:
        vec_records = [
            VectorRecord(
                id=f"kb:{article_id}:{c.chunk_order}",
                target_type="kb_chunk",
                target_id=f"{article_id}:{c.chunk_order}",
                text=c.text,
                metadata={
                    "article_id": article_id,
                    "article_title": title,
                    "section_title": c.section_title or "",
                    "module": module or "",
                },
                vector=v,
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        try:
            await vector_store.upsert(vec_records)
        except Exception as e:  # noqa: BLE001
            logger.warning("kb.vector_upsert_failed", article_id=article_id, error=str(e))

    fts_records = [
        TextSearchRecord(
            id=f"kb:{article_id}:{c.chunk_order}",
            target_type="kb_chunk",
            target_id=f"{article_id}:{c.chunk_order}",
            title=(title + (" — " + c.section_title if c.section_title else ""))[:200],
            content=c.text,
        )
        for c in chunks
    ]
    try:
        await text_search.upsert(fts_records)
    except Exception as e:  # noqa: BLE001
        logger.warning("kb.text_search_failed", article_id=article_id, error=str(e))

    return article_id


async def delete_article_index(
    *,
    article_id: str,
    chunk_ids: list[str],
    vector_store: VectorStore,
    text_search: TextSearch,
) -> None:
    """Удаляет все векторы/FTS-записи для удалённой статьи."""
    try:
        await vector_store.delete_by_target("kb_chunk", chunk_ids)
    except Exception as e:  # noqa: BLE001
        logger.warning("kb.vector_delete_failed", article_id=article_id, error=str(e))
    try:
        await text_search.delete_by_target("kb_chunk", chunk_ids)
    except Exception as e:  # noqa: BLE001
        logger.warning("kb.text_search_delete_failed", article_id=article_id, error=str(e))
