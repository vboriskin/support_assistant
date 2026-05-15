# 07. Adapters: Vector Store

Векторное хранилище — это абстракция над таблицей, где живут эмбеддинги. Должно работать в двух режимах:
- **SQLite + sqlite-vec** — для локального запуска, MVP, разработки. Никаких внешних зависимостей.
- **Postgres + pgvector** — для прода и масштабирования.

Выбор — автоматический по `DB_BACKEND`, можно переопределить через `VECTOR_STORE_BACKEND`.

## Базовый интерфейс

`adapters/vector_store/base.py`:

```python
from typing import Protocol
from pydantic import BaseModel

class VectorRecord(BaseModel):
    """Запись в индексе."""
    id: str
    target_type: str            # "kb_chunk" | "ticket_summary" | "ticket_symptom"
    target_id: str
    text: str                   # сам текст, что эмбеддили (для отладки)
    metadata: dict = {}
    vector: list[float]

class VectorSearchHit(BaseModel):
    """Результат поиска."""
    id: str
    target_type: str
    target_id: str
    text: str
    metadata: dict
    score: float                # cosine similarity, [0..1]

class VectorStore(Protocol):
    """Абстракция векторного хранилища."""

    async def upsert(self, records: list[VectorRecord]) -> None:
        """Вставить или обновить записи."""

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        """Удалить все векторы для перечисленных target_id."""

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
        metadata_filters: dict | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]:
        """Векторный поиск с опциональными фильтрами по метаданным."""

    async def count(self, target_type: str | None = None) -> int:
        """Сколько векторов в индексе."""

    async def health(self) -> bool:
        """Доступен ли индекс."""
```

## SQLite + sqlite-vec

`adapters/vector_store/sqlite_vec_store.py`. Использует расширение `sqlite-vec` (pip-пакет `sqlite-vec`), которое подгружается в соединение.

```python
import json
import sqlite3
import struct
from typing import Any
import structlog
import sqlite_vec
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import VectorStore, VectorRecord, VectorSearchHit
from config.settings import Settings

logger = structlog.get_logger(__name__)


def _serialize_vector(v: list[float]) -> bytes:
    """sqlite-vec ожидает векторы в виде float32 little-endian."""
    return struct.pack(f"{len(v)}f", *v)


class SQLiteVecStore:
    """Векторное хранилище на SQLite + sqlite-vec."""

    def __init__(self, settings: Settings, engine: AsyncEngine):
        self.settings = settings
        self._engine = engine
        self._dim = settings.embeddings.dimension

    async def _ensure_schema(self) -> None:
        """Создаёт виртуальную таблицу и доп. таблицу метаданных, если их нет."""
        async with self._engine.begin() as conn:
            # Загружаем sqlite-vec в сырое sqlite-соединение
            raw = await conn.get_raw_connection()
            sqlite_vec.load(raw.connection)
            # Виртуальная таблица для векторов
            await conn.execute(text(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[{self._dim}]
                )
            """))
            # Таблица метаданных (sqlite-vec не хранит метаданные)
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS embeddings_meta (
                    id TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT
                )
            """))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_emb_target ON embeddings_meta(target_type, target_id)"
            ))

    async def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            raw = await conn.get_raw_connection()
            sqlite_vec.load(raw.connection)
            for r in records:
                if len(r.vector) != self._dim:
                    raise ValueError(
                        f"Vector dimension {len(r.vector)} != expected {self._dim}"
                    )
                vec_blob = _serialize_vector(r.vector)
                # vec0 не поддерживает прямой UPSERT — DELETE + INSERT
                await conn.execute(text("DELETE FROM vec_embeddings WHERE id = :id"), {"id": r.id})
                await conn.execute(
                    text("INSERT INTO vec_embeddings (id, embedding) VALUES (:id, :v)"),
                    {"id": r.id, "v": vec_blob},
                )
                await conn.execute(text("""
                    INSERT INTO embeddings_meta (id, target_type, target_id, text, metadata_json)
                    VALUES (:id, :tt, :tid, :tx, :md)
                    ON CONFLICT(id) DO UPDATE SET
                        target_type=excluded.target_type,
                        target_id=excluded.target_id,
                        text=excluded.text,
                        metadata_json=excluded.metadata_json
                """), {
                    "id": r.id, "tt": r.target_type, "tid": r.target_id,
                    "tx": r.text, "md": json.dumps(r.metadata, ensure_ascii=False),
                })

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        if not target_ids:
            return 0
        async with self._engine.begin() as conn:
            # Сначала находим id, потом удаляем
            placeholders = ",".join(f":id{i}" for i in range(len(target_ids)))
            params = {f"id{i}": tid for i, tid in enumerate(target_ids)}
            params["tt"] = target_type
            rows = await conn.execute(text(f"""
                SELECT id FROM embeddings_meta
                WHERE target_type = :tt AND target_id IN ({placeholders})
            """), params)
            ids = [r[0] for r in rows.fetchall()]
            if not ids:
                return 0
            ph2 = ",".join(f":i{i}" for i in range(len(ids)))
            params2 = {f"i{i}": _id for i, _id in enumerate(ids)}
            await conn.execute(text(f"DELETE FROM vec_embeddings WHERE id IN ({ph2})"), params2)
            await conn.execute(text(f"DELETE FROM embeddings_meta WHERE id IN ({ph2})"), params2)
            return len(ids)

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
        metadata_filters: dict | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]:
        await self._ensure_schema()
        vec_blob = _serialize_vector(query_vector)
        # vec0 возвращает distance (меньше = ближе). Конвертируем в similarity.
        # Если векторы нормализованы (что мы делаем) и метрика — L2,
        # то similarity ≈ 1 - distance² / 2
        async with self._engine.connect() as conn:
            raw = await conn.get_raw_connection()
            sqlite_vec.load(raw.connection)
            # KNN-поиск через vec0
            rows = await conn.execute(text(f"""
                SELECT v.id, v.distance, m.target_type, m.target_id, m.text, m.metadata_json
                FROM vec_embeddings v
                JOIN embeddings_meta m ON v.id = m.id
                WHERE v.embedding MATCH :q
                  AND k = :k
                ORDER BY v.distance
            """), {"q": vec_blob, "k": top_k * 3})    # берём с запасом для фильтрации
            results: list[VectorSearchHit] = []
            for row in rows:
                tt = row.target_type
                if target_types and tt not in target_types:
                    continue
                md = json.loads(row.metadata_json) if row.metadata_json else {}
                if metadata_filters:
                    if not all(md.get(k) == v for k, v in metadata_filters.items()):
                        continue
                # L2 → cosine similarity (для нормализованных векторов)
                distance = float(row.distance)
                score = max(0.0, 1.0 - (distance ** 2) / 2)
                if score < min_score:
                    continue
                results.append(VectorSearchHit(
                    id=row.id, target_type=tt, target_id=row.target_id,
                    text=row.text, metadata=md, score=score,
                ))
                if len(results) >= top_k:
                    break
            return results

    async def count(self, target_type: str | None = None) -> int:
        async with self._engine.connect() as conn:
            if target_type:
                row = await conn.execute(
                    text("SELECT COUNT(*) FROM embeddings_meta WHERE target_type = :tt"),
                    {"tt": target_type},
                )
            else:
                row = await conn.execute(text("SELECT COUNT(*) FROM embeddings_meta"))
            return row.scalar() or 0

    async def health(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
```

### Загрузка sqlite-vec в соединение

Важная деталь: `sqlite-vec` — это динамическое расширение. Его нужно загружать **в каждое соединение** перед использованием. Для SQLAlchemy с aiosqlite — через event listener:

```python
# db/engine.py
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import event
import sqlite_vec

def create_engine_with_vec(url: str):
    engine = create_async_engine(url, future=True)
    sync_engine = engine.sync_engine
    @event.listens_for(sync_engine, "connect")
    def _load_vec(dbapi_conn, _):
        dbapi_conn.enable_load_extension(True)
        sqlite_vec.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)
    return engine
```

## Postgres + pgvector

`adapters/vector_store/pgvector_store.py`. Использует расширение `pgvector` в Postgres.

```python
import json
from typing import Any
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .base import VectorStore, VectorRecord, VectorSearchHit
from config.settings import Settings

logger = structlog.get_logger(__name__)


class PgVectorStore:
    """Векторное хранилище на Postgres + pgvector."""

    def __init__(self, settings: Settings, engine: AsyncEngine):
        self.settings = settings
        self._engine = engine
        self._dim = settings.embeddings.dimension

    async def _ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json JSONB,
                    vector vector({self._dim}) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_target ON embeddings(target_type, target_id)"
            ))
            # ivfflat (компромисс скорость/качество)
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_embeddings_vector
                ON embeddings USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)
            """))

    async def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            for r in records:
                if len(r.vector) != self._dim:
                    raise ValueError(
                        f"Vector dimension {len(r.vector)} != expected {self._dim}"
                    )
                vec_literal = "[" + ",".join(str(x) for x in r.vector) + "]"
                await conn.execute(text("""
                    INSERT INTO embeddings (id, target_type, target_id, text, metadata_json, vector)
                    VALUES (:id, :tt, :tid, :tx, CAST(:md AS jsonb), CAST(:v AS vector))
                    ON CONFLICT (id) DO UPDATE SET
                        target_type = EXCLUDED.target_type,
                        target_id = EXCLUDED.target_id,
                        text = EXCLUDED.text,
                        metadata_json = EXCLUDED.metadata_json,
                        vector = EXCLUDED.vector
                """), {
                    "id": r.id, "tt": r.target_type, "tid": r.target_id,
                    "tx": r.text, "md": json.dumps(r.metadata, ensure_ascii=False),
                    "v": vec_literal,
                })

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        if not target_ids:
            return 0
        async with self._engine.begin() as conn:
            res = await conn.execute(text("""
                DELETE FROM embeddings
                WHERE target_type = :tt AND target_id = ANY(:ids)
            """), {"tt": target_type, "ids": target_ids})
            return res.rowcount or 0

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
        metadata_filters: dict | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]:
        await self._ensure_schema()
        vec_literal = "[" + ",".join(str(x) for x in query_vector) + "]"

        # Собираем WHERE-условия
        where_parts = []
        params: dict[str, Any] = {"q": vec_literal, "k": top_k * 3}
        if target_types:
            where_parts.append("target_type = ANY(:tts)")
            params["tts"] = target_types
        if metadata_filters:
            for i, (k, v) in enumerate(metadata_filters.items()):
                where_parts.append(f"metadata_json ->> :mk{i} = :mv{i}")
                params[f"mk{i}"] = k
                params[f"mv{i}"] = str(v)
        where_clause = " AND ".join(where_parts) if where_parts else "TRUE"

        async with self._engine.connect() as conn:
            rows = await conn.execute(text(f"""
                SELECT id, target_type, target_id, text, metadata_json,
                       1 - (vector <=> CAST(:q AS vector)) AS similarity
                FROM embeddings
                WHERE {where_clause}
                ORDER BY vector <=> CAST(:q AS vector)
                LIMIT :k
            """), params)
            results: list[VectorSearchHit] = []
            for row in rows:
                score = float(row.similarity)
                if score < min_score:
                    continue
                md = row.metadata_json or {}
                if isinstance(md, str):
                    md = json.loads(md)
                results.append(VectorSearchHit(
                    id=row.id, target_type=row.target_type, target_id=row.target_id,
                    text=row.text, metadata=md, score=score,
                ))
                if len(results) >= top_k:
                    break
            return results

    async def count(self, target_type: str | None = None) -> int:
        async with self._engine.connect() as conn:
            if target_type:
                row = await conn.execute(
                    text("SELECT COUNT(*) FROM embeddings WHERE target_type = :tt"),
                    {"tt": target_type},
                )
            else:
                row = await conn.execute(text("SELECT COUNT(*) FROM embeddings"))
            return row.scalar() or 0

    async def health(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
```

### `ivfflat` vs `hnsw`

`pgvector` поддерживает два типа индексов:
- **ivfflat** — быстрее в индексации, чуть медленнее в поиске. Параметр `lists` — корень из числа строк.
- **hnsw** — лучше recall, дороже по памяти. Параметры `m`, `ef_construction`.

Для MVP — `ivfflat` с `lists=100`. Если индекс вырастет до сотен тысяч — переход на `hnsw`.

## Factory

```python
# adapters/vector_store/factory.py
from .base import VectorStore
from .sqlite_vec_store import SQLiteVecStore
from .pgvector_store import PgVectorStore
from config.settings import Settings
from db.engine import get_engine

def create_vector_store(settings: Settings) -> VectorStore:
    backend = settings.vector_store.backend or (
        "pgvector" if settings.db.backend == "postgres" else "sqlite_vec"
    )
    engine = get_engine()
    if backend == "sqlite_vec":
        return SQLiteVecStore(settings, engine)
    if backend == "pgvector":
        return PgVectorStore(settings, engine)
    raise ValueError(f"Unknown vector store backend: {backend}")
```

## Text Search (полнотекстовый поиск)

Аналогичная абстракция для FTS — описана кратко, реализация по аналогии.

`adapters/text_search/base.py`:

```python
from typing import Protocol
from pydantic import BaseModel

class TextSearchHit(BaseModel):
    id: str
    target_type: str
    target_id: str
    title: str
    content: str
    score: float                # BM25 / ts_rank

class TextSearch(Protocol):
    async def upsert(self, records: list[dict]) -> None: ...
    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int: ...
    async def search(
        self, query: str, *, top_k: int = 30,
        target_types: list[str] | None = None,
    ) -> list[TextSearchHit]: ...
```

### SQLite FTS5

Виртуальная таблица `text_search` с токенизатором `unicode61`. Поиск через `MATCH`:

```sql
CREATE VIRTUAL TABLE text_search USING fts5(
    target_type UNINDEXED,
    target_id UNINDEXED,
    title,
    content,
    tokenize = 'unicode61 remove_diacritics 1'
);

-- Поиск:
SELECT target_type, target_id, title, content,
       bm25(text_search) AS score
FROM text_search
WHERE text_search MATCH :query
ORDER BY score
LIMIT :k;
```

### Postgres FTS

С `tsvector` и `tsquery`:

```sql
SELECT target_type, target_id, title, content,
       ts_rank(tsv, plainto_tsquery('russian', :query)) AS score
FROM text_search
WHERE tsv @@ plainto_tsquery('russian', :query)
ORDER BY score DESC
LIMIT :k;
```

Обе реализации возвращают `TextSearchHit`. В retrieval (см. `10-RETRIEVAL.md`) результаты двух поисков объединяются через RRF.

## Реиндексация и инвалидация

При изменении выжимки тикета или статьи KB:
1. Удалить старые эмбеддинги: `delete_by_target("ticket_summary", [ticket_id])`.
2. Удалить из text_search: `delete_by_target` (другого адаптера).
3. Сгенерировать новые эмбеддинги, проиндексировать.

При смене модели эмбеддингов — полная переиндексация всех записей. Скрипт `scripts/reindex.py`.

## Тесты

См. `18-TESTING.md`. Минимум:

- Upsert + count + search round-trip.
- Поиск с фильтром по target_type.
- Поиск с фильтром по metadata.
- delete_by_target удаляет указанные.
- Идентичность результата для SQLiteVec и Pgvector на одних и тех же данных (с допустимым отклонением score).
