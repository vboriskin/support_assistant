"""Полнотекстовый поиск на Postgres tsvector + plainto_tsquery.

Структура — таблица ``text_search`` с генерируемой колонкой ``tsv``:
``setweight(A) title || setweight(B) content``, индекс GIN. Запросы строим
через ``plainto_tsquery('russian', ...)``.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from config.logging import get_logger
from config.settings import Settings

from .base import TextSearchHit, TextSearchRecord

logger = get_logger("adapters.text_search.postgres_fts")

_LANG = "russian"


class PostgresFTS:
    def __init__(self, settings: Settings, engine: AsyncEngine) -> None:
        self._settings = settings
        self._engine = engine
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS text_search ("
                    " id TEXT PRIMARY KEY,"
                    " target_type TEXT NOT NULL,"
                    " target_id TEXT NOT NULL,"
                    " title TEXT NOT NULL,"
                    " content TEXT NOT NULL,"
                    f" tsv tsvector GENERATED ALWAYS AS ("
                    f"   setweight(to_tsvector('{_LANG}', coalesce(title,'')), 'A') ||"
                    f"   setweight(to_tsvector('{_LANG}', coalesce(content,'')), 'B')"
                    f" ) STORED"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_text_search_tsv "
                    "ON text_search USING gin(tsv)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_text_search_target "
                    "ON text_search(target_type, target_id)"
                )
            )
        self._schema_ready = True

    async def upsert(self, records: list[TextSearchRecord]) -> None:
        if not records:
            return
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            for r in records:
                await conn.execute(
                    text(
                        "INSERT INTO text_search (id, target_type, target_id, title, content) "
                        "VALUES (:id, :tt, :tid, :title, :content) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "  target_type = EXCLUDED.target_type, "
                        "  target_id = EXCLUDED.target_id, "
                        "  title = EXCLUDED.title, "
                        "  content = EXCLUDED.content"
                    ),
                    {
                        "id": r.id,
                        "tt": r.target_type,
                        "tid": r.target_id,
                        "title": r.title,
                        "content": r.content,
                    },
                )

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        if not target_ids:
            return 0
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                text(
                    "DELETE FROM text_search "
                    "WHERE target_type = :tt AND target_id = ANY(:ids)"
                ),
                {"tt": target_type, "ids": target_ids},
            )
            return res.rowcount or 0

    async def search(
        self,
        query: str,
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
    ) -> list[TextSearchHit]:
        await self._ensure_schema()
        if not query.strip():
            return []
        where_parts = [f"tsv @@ plainto_tsquery('{_LANG}', :q)"]
        params: dict[str, object] = {"q": query, "k": top_k * 3}
        if target_types:
            where_parts.append("target_type = ANY(:tts)")
            params["tts"] = target_types
        where_clause = " AND ".join(where_parts)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT id, target_type, target_id, title, content, "
                        f"       ts_rank(tsv, plainto_tsquery('{_LANG}', :q)) AS rank "
                        f"FROM text_search "
                        f"WHERE {where_clause} "
                        f"ORDER BY rank DESC "
                        f"LIMIT :k"
                    ),
                    params,
                )
            ).mappings().all()

        results: list[TextSearchHit] = []
        for row in rows:
            results.append(
                TextSearchHit(
                    id=row["id"],
                    target_type=row["target_type"],
                    target_id=row["target_id"],
                    title=row["title"],
                    content=row["content"],
                    score=float(row["rank"]),
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
                    text("SELECT COUNT(*) FROM text_search WHERE target_type = :tt"),
                    {"tt": target_type},
                )
            else:
                row = await conn.execute(text("SELECT COUNT(*) FROM text_search"))
            return int(row.scalar() or 0)
