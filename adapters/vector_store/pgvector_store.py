"""Векторное хранилище на Postgres + pgvector.

Использует одну таблицу ``embeddings`` (с колонкой ``vector(N)``) и ``ivfflat``-индекс
с ``vector_cosine_ops``. ``cosine_similarity`` берём как ``1 - (v <=> q)``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from config.logging import get_logger
from config.settings import Settings

from .base import VectorRecord, VectorSearchHit

logger = get_logger("adapters.vector_store.pgvector")


def _vector_literal(v: list[float]) -> str:
    """pgvector принимает текстовый литерал вида '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


class PgVectorStore:
    def __init__(self, settings: Settings, engine: AsyncEngine) -> None:
        self._settings = settings
        self._engine = engine
        self._dim = settings.embeddings.dimension
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS embeddings ("
                    f"  id TEXT PRIMARY KEY,"
                    f"  target_type TEXT NOT NULL,"
                    f"  target_id TEXT NOT NULL,"
                    f"  text TEXT NOT NULL,"
                    f"  metadata_json JSONB,"
                    f"  vector vector({self._dim}) NOT NULL,"
                    f"  created_at TIMESTAMP DEFAULT NOW()"
                    f")"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_embeddings_target "
                    "ON embeddings(target_type, target_id)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_embeddings_vector "
                    "ON embeddings USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)"
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
                await conn.execute(
                    text(
                        "INSERT INTO embeddings (id, target_type, target_id, text, metadata_json, vector) "
                        "VALUES (:id, :tt, :tid, :tx, CAST(:md AS jsonb), CAST(:v AS vector)) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "  target_type = EXCLUDED.target_type, "
                        "  target_id = EXCLUDED.target_id, "
                        "  text = EXCLUDED.text, "
                        "  metadata_json = EXCLUDED.metadata_json, "
                        "  vector = EXCLUDED.vector"
                    ),
                    {
                        "id": r.id,
                        "tt": r.target_type,
                        "tid": r.target_id,
                        "tx": r.text,
                        "md": json.dumps(r.metadata, ensure_ascii=False),
                        "v": _vector_literal(r.vector),
                    },
                )

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        if not target_ids:
            return 0
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                text(
                    "DELETE FROM embeddings "
                    "WHERE target_type = :tt AND target_id = ANY(:ids)"
                ),
                {"tt": target_type, "ids": target_ids},
            )
            return res.rowcount or 0

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
        where_parts: list[str] = []
        params: dict[str, Any] = {"q": _vector_literal(query_vector), "k": top_k * 3}
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
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, target_type, target_id, text, metadata_json, "
                        f"       1 - (vector <=> CAST(:q AS vector)) AS similarity "
                        f"FROM embeddings "
                        f"WHERE {where_clause} "
                        f"ORDER BY vector <=> CAST(:q AS vector) "
                        f"LIMIT :k"
                    ),
                    params,
                )
            ).mappings().all()

        results: list[VectorSearchHit] = []
        for row in rows:
            score = float(row["similarity"])
            if score < min_score:
                continue
            md = row["metadata_json"] or {}
            if isinstance(md, str):
                md = json.loads(md)
            results.append(
                VectorSearchHit(
                    id=row["id"],
                    target_type=row["target_type"],
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
                    text("SELECT COUNT(*) FROM embeddings WHERE target_type = :tt"),
                    {"tt": target_type},
                )
            else:
                row = await conn.execute(text("SELECT COUNT(*) FROM embeddings"))
            return int(row.scalar() or 0)

    async def health(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning("pgvector.health_failed", error=str(e))
            return False
