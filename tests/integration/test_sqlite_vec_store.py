"""Интеграционные тесты ``SQLiteVecStore``.

Тесты используют небольшую размерность (32) и mock-эмбеддинги — это держит
прогон быстрым и стабильным.

На macOS python.org-сборке ``sqlite3`` собран без ``enable_load_extension``,
из-за чего ``sqlite-vec`` не загружается. В таком случае весь модуль
пропускается: соответствующий стенд проверит тесты на Linux / Docker.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from adapters.embeddings.mock import MockEmbeddingsClient
from adapters.vector_store.sqlite_vec_store import SQLiteVecStore
from adapters.vector_store.base import VectorRecord
from config.settings import Settings

_HAS_LOAD_EXT = hasattr(sqlite3.connect(":memory:"), "enable_load_extension")
try:
    import sqlite_vec  # noqa: F401

    _HAS_VEC = True
except ImportError:
    _HAS_VEC = False

# Реальная проверка: можно ли подгрузить расширение И создать vec0-таблицу.
# На Ubuntu CI оба флага выше могут быть True, но sqlite-build из репозитория
# не подружается с пакетом sqlite_vec из-за несовместимости версий — в этом
# случае пытаемся создать вирт-таблицу и ловим ошибку.
_HAS_VEC0 = False
if _HAS_LOAD_EXT and _HAS_VEC:
    try:
        import sqlite_vec as _sv

        _c = sqlite3.connect(":memory:")
        _c.enable_load_extension(True)
        _sv.load(_c)
        _c.execute("CREATE VIRTUAL TABLE _probe USING vec0(id TEXT PRIMARY KEY, v float[3])")
        _c.close()
        _HAS_VEC0 = True
    except Exception:  # noqa: BLE001
        _HAS_VEC0 = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _HAS_VEC0,
        reason="sqlite3 без load_extension / sqlite_vec / vec0-биндинга — "
        "проверяется на Linux/контурном стенде с совместимым sqlite_vec",
    ),
]

DIM = 32


def _settings_with_dim() -> Settings:
    s = Settings()
    object.__setattr__(s.embeddings, "dimension", DIM)
    return s


def _embed(emb: MockEmbeddingsClient, text: str) -> list[float]:
    # синхронный mock-вектор для удобства подготовки фикстур
    return emb._vector(text)


@pytest.fixture
async def store(vec_engine: AsyncEngine) -> SQLiteVecStore:
    return SQLiteVecStore(_settings_with_dim(), vec_engine)


async def test_upsert_search_round_trip(store: SQLiteVecStore) -> None:
    emb = MockEmbeddingsClient(dimension=DIM)
    records = [
        VectorRecord(
            id=f"id-{i}",
            target_type="ticket_summary",
            target_id=f"t-{i}",
            text=f"text {i}",
            metadata={"module": "loan" if i % 2 == 0 else "scoring"},
            vector=_embed(emb, f"text {i}"),
        )
        for i in range(100)
    ]
    await store.upsert(records)
    assert await store.count() == 100
    assert await store.count(target_type="ticket_summary") == 100

    # ищем по тексту, ровно совпадающему с одним из элементов — он должен быть в топе
    target_idx = 17
    q = _embed(emb, f"text {target_idx}")
    hits = await store.search(q, top_k=5)
    assert len(hits) > 0
    assert hits[0].id == f"id-{target_idx}"
    assert hits[0].score > 0.99  # совпадение почти идеальное


async def test_filter_by_target_type(store: SQLiteVecStore) -> None:
    emb = MockEmbeddingsClient(dimension=DIM)
    records = [
        VectorRecord(
            id="kb-1",
            target_type="kb_chunk",
            target_id="kb-art-1",
            text="how to upload PDF",
            metadata={},
            vector=_embed(emb, "kb pdf upload"),
        ),
        VectorRecord(
            id="ts-1",
            target_type="ticket_summary",
            target_id="t-1",
            text="ticket about PDF upload",
            metadata={},
            vector=_embed(emb, "kb pdf upload"),
        ),
    ]
    await store.upsert(records)

    q = _embed(emb, "kb pdf upload")
    all_hits = await store.search(q, top_k=10)
    assert {h.target_type for h in all_hits} == {"kb_chunk", "ticket_summary"}

    only_kb = await store.search(q, top_k=10, target_types=["kb_chunk"])
    assert [h.target_type for h in only_kb] == ["kb_chunk"]


async def test_filter_by_metadata(store: SQLiteVecStore) -> None:
    emb = MockEmbeddingsClient(dimension=DIM)
    records = [
        VectorRecord(
            id=f"id-{i}",
            target_type="ticket_summary",
            target_id=f"t-{i}",
            text=f"text {i}",
            metadata={"module": "loan" if i < 3 else "scoring"},
            vector=_embed(emb, f"text {i}"),
        )
        for i in range(6)
    ]
    await store.upsert(records)

    q = _embed(emb, "text 0")
    loan_hits = await store.search(q, top_k=20, metadata_filters={"module": "loan"})
    assert {h.metadata["module"] for h in loan_hits} == {"loan"}
    assert len(loan_hits) == 3


async def test_delete_by_target(store: SQLiteVecStore) -> None:
    emb = MockEmbeddingsClient(dimension=DIM)
    records = [
        VectorRecord(
            id=f"id-{i}",
            target_type="ticket_summary",
            target_id=f"t-{i // 2}",  # по два эмбеддинга на тикет
            text=f"text {i}",
            metadata={},
            vector=_embed(emb, f"text {i}"),
        )
        for i in range(6)
    ]
    await store.upsert(records)
    assert await store.count() == 6

    removed = await store.delete_by_target("ticket_summary", ["t-0", "t-1"])
    assert removed == 4
    assert await store.count() == 2

    # повторное удаление — 0
    assert await store.delete_by_target("ticket_summary", ["t-0"]) == 0


async def test_upsert_updates_existing_record(store: SQLiteVecStore) -> None:
    emb = MockEmbeddingsClient(dimension=DIM)
    rec = VectorRecord(
        id="x",
        target_type="kb_chunk",
        target_id="art-1",
        text="alpha",
        metadata={"v": 1},
        vector=_embed(emb, "alpha"),
    )
    await store.upsert([rec])
    rec2 = VectorRecord(
        id="x",
        target_type="kb_chunk",
        target_id="art-1",
        text="beta",
        metadata={"v": 2},
        vector=_embed(emb, "beta"),
    )
    await store.upsert([rec2])
    assert await store.count() == 1

    hits = await store.search(_embed(emb, "beta"), top_k=1)
    assert hits[0].text == "beta"
    assert hits[0].metadata == {"v": 2}


async def test_dimension_mismatch_raises(store: SQLiteVecStore) -> None:
    rec = VectorRecord(
        id="z",
        target_type="kb_chunk",
        target_id="art-1",
        text="x",
        metadata={},
        vector=[0.0] * (DIM - 1),
    )
    with pytest.raises(ValueError, match="dimension"):
        await store.upsert([rec])


async def test_health(store: SQLiteVecStore) -> None:
    assert await store.health() is True
