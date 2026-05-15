"""Векторное хранилище на SQLite + sqlite-vec.

Архитектура:

- ``vec_embeddings`` — виртуальная ``vec0``-таблица только с ``id`` и вектором.
- ``embeddings_meta`` — обычная таблица с ``target_type``, ``target_id``,
  ``text`` и JSON-метаданными. ``vec0`` не умеет хранить произвольные поля,
  поэтому метаданные держим рядом.

``sqlite-vec`` загружается в соединение через event-listener в
``db/engine.py``. Здесь — никаких ``sqlite_vec.load()`` вручную.

Score-метрика: ``vec0`` возвращает L2-distance. Для нормализованных векторов
(а у нас они всегда нормализованы) ``cosine_similarity ≈ 1 - distance² / 2``.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from config.logging import get_logger
from config.settings import Settings

from .base import VectorRecord, VectorSearchHit

logger = get_logger("adapters.vector_store.sqlite_vec")


def _serialize_vector(v: list[float]) -> bytes:
    """sqlite-vec ожидает float32 little-endian."""
    return struct.pack(f"<{len(v)}f", *v)


class SQLiteVecStore:
    def __init__(self, settings: Settings, engine: AsyncEngine) -> None:
        self._settings = settings
        self._engine = engine
        self._dim = settings.embeddings.dimension
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0("
                    f"  id TEXT PRIMARY KEY,"
                    f"  embedding float[{self._dim}]"
                    f")"
                )
            )
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS embeddings_meta ("
                    " id TEXT PRIMARY KEY,"
                    " target_type TEXT NOT NULL,"
                    " target_id TEXT NOT NULL,"
                    " text TEXT NOT NULL,"
                    " metadata_json TEXT"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_emb_meta_target "
                    "ON embeddings_meta(target_type, target_id)"
                )
            )
        self._schema_ready = True

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
                vec_blob = _serialize_vector(r.vector)
                # vec0 не поддерживает UPSERT — делаем DELETE+INSERT.
                await conn.execute(
                    text("DELETE FROM vec_embeddings WHERE id = :id"), {"id": r.id}
                )
                await conn.execute(
                    text("INSERT INTO vec_embeddings (id, embedding) VALUES (:id, :v)"),
                    {"id": r.id, "v": vec_blob},
                )
                await conn.execute(
                    text(
                        "INSERT INTO embeddings_meta (id, target_type, target_id, text, metadata_json) "
                        "VALUES (:id, :tt, :tid, :tx, :md) "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "  target_type=excluded.target_type, "
                        "  target_id=excluded.target_id, "
                        "  text=excluded.text, "
                        "  metadata_json=excluded.metadata_json"
                    ),
                    {
                        "id": r.id,
                        "tt": r.target_type,
                        "tid": r.target_id,
                        "tx": r.text,
                        "md": json.dumps(r.metadata, ensure_ascii=False),
                    },
                )

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        if not target_ids:
            return 0
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            placeholders = ", ".join(f":id{i}" for i in range(len(target_ids)))
            params: dict[str, Any] = {f"id{i}": tid for i, tid in enumerate(target_ids)}
            params["tt"] = target_type
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id FROM embeddings_meta "
                        f"WHERE target_type = :tt AND target_id IN ({placeholders})"
                    ),
                    params,
                )
            ).fetchall()
            ids = [r[0] for r in rows]
            if not ids:
                return 0
            ph2 = ", ".join(f":i{i}" for i in range(len(ids)))
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
        metadata_filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]:
        await self._ensure_schema()
        if len(query_vector) != self._dim:
            raise ValueError(
                f"Query dimension {len(query_vector)} != expected {self._dim}"
            )
        vec_blob = _serialize_vector(query_vector)
        # Берём кандидатов с запасом — пост-фильтры по target_type / metadata
        # могут отсечь часть результатов.
        overfetch = max(top_k * 3, top_k + 10)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT v.id AS id, v.distance AS distance, "
                        "       m.target_type AS target_type, m.target_id AS target_id, "
                        "       m.text AS text, m.metadata_json AS metadata_json "
                        "FROM vec_embeddings v "
                        "JOIN embeddings_meta m ON v.id = m.id "
                        "WHERE v.embedding MATCH :q AND k = :k "
                        "ORDER BY v.distance"
                    ),
                    {"q": vec_blob, "k": overfetch},
                )
            ).mappings().all()

        results: list[VectorSearchHit] = []
        for row in rows:
            tt = row["target_type"]
            if target_types and tt not in target_types:
                continue
            md = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
            if metadata_filters and not all(md.get(k) == v for k, v in metadata_filters.items()):
                continue
            distance = float(row["distance"])
            # Для L2-нормы по нормализованным векторам cosine ≈ 1 - d²/2.
            score = max(0.0, 1.0 - (distance**2) / 2.0)
            if score < min_score:
                continue
            results.append(
                VectorSearchHit(
                    id=row["id"],
                    target_type=tt,
                    target_id=row["target_id"],
                    text=row["text"],
                    metadata=md,
                    score=score,
                )
            )
            if len(results) >= top_k:
                break
        return results

    async def count(self, target_type: str | None = None) -> int:
        await self._ensure_schema()
        async with self._engine.connect() as conn:
            if target_type:
                row = await conn.execute(
                    text("SELECT COUNT(*) FROM embeddings_meta WHERE target_type = :tt"),
                    {"tt": target_type},
                )
            else:
                row = await conn.execute(text("SELECT COUNT(*) FROM embeddings_meta"))
            return int(row.scalar() or 0)

    async def health(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning("sqlite_vec.health_failed", error=str(e))
            return False
