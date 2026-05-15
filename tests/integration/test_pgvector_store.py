"""Smoke-тест ``PgVectorStore``.

Если в окружении задана ``TEST_POSTGRES_URL`` (формат
``postgresql+asyncpg://user:pass@host:port/db``) и расширение ``vector``
установлено в БД — гоняем полный round-trip. Иначе тест аккуратно
пропускается: настоящий стенд (Linux, контурный Postgres) выполнит проверку.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from adapters.embeddings.mock import MockEmbeddingsClient
from adapters.vector_store.base import VectorRecord
from adapters.vector_store.pgvector_store import PgVectorStore
from config.settings import Settings

_PG_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _PG_URL,
        reason="TEST_POSTGRES_URL не задан — pgvector проверяется на стенде",
    ),
]

DIM = 32


@pytest.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    assert _PG_URL is not None
    engine = create_async_engine(_PG_URL, future=True)
    yield engine
    # подчищаем за собой, чтобы повторные прогоны не накапливали мусор
    from sqlalchemy import text as _text

    async with engine.begin() as conn:
        await conn.execute(_text("DROP TABLE IF EXISTS embeddings"))
    await engine.dispose()


@pytest.fixture
async def store(pg_engine: AsyncEngine) -> PgVectorStore:
    s = Settings()
    object.__setattr__(s.embeddings, "dimension", DIM)
    return PgVectorStore(s, pg_engine)


async def test_upsert_search_filter_delete(store: PgVectorStore) -> None:
    emb = MockEmbeddingsClient(dimension=DIM)
    records = [
        VectorRecord(
            id=f"id-{i}",
            target_type="ticket_summary" if i % 2 == 0 else "kb_chunk",
            target_id=f"t-{i}",
            text=f"text {i}",
            metadata={"module": "loan" if i < 5 else "scoring"},
            vector=emb._vector(f"text {i}"),
        )
        for i in range(20)
    ]
    await store.upsert(records)
    assert await store.count() == 20

    target_idx = 7
    hits = await store.search(emb._vector(f"text {target_idx}"), top_k=3)
    assert hits[0].id == f"id-{target_idx}"

    loan_only = await store.search(
        emb._vector("text 0"), top_k=20, metadata_filters={"module": "loan"}
    )
    assert {h.metadata.get("module") for h in loan_only} == {"loan"}

    kb_only = await store.search(
        emb._vector("text 0"), top_k=20, target_types=["kb_chunk"]
    )
    assert all(h.target_type == "kb_chunk" for h in kb_only)

    removed = await store.delete_by_target("ticket_summary", [f"t-{i}" for i in (0, 2, 4)])
    assert removed == 3
