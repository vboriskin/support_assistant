"""Полнотекстовый поиск на SQLite FTS5.

Структура: одна виртуальная FTS5-таблица. ``target_type`` и ``target_id`` —
``UNINDEXED``, чтобы их можно было хранить, но не включать в BM25-индекс.
Токенизация — ``unicode61 remove_diacritics 1`` (нормально работает с
русским).

BM25 в SQLite возвращается через функцию ``bm25(table)``: чем меньше — тем
лучше. Мы инвертируем в положительное «better is higher», чтобы remix
с векторным score'ом в retrieval был интуитивнее.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from config.logging import get_logger
from config.settings import Settings

from .base import TextSearchHit, TextSearchRecord

logger = get_logger("adapters.text_search.sqlite_fts")


# FTS5 MATCH-синтаксис чувствителен к спецсимволам (кавычки, скобки, ?, *,
# двоеточия и т.п.). Пользовательский запрос — обычная фраза, операторы AND/OR
# нам не нужны. Самая безопасная стратегия — оставить только буквы, цифры и
# пробелы (включая кириллицу).
_KEEP_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def _sanitize_query(q: str) -> str:
    cleaned = _KEEP_RE.sub(" ", q)
    return " ".join(cleaned.split())


class SQLiteFTS5:
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
                    "CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5("
                    " id UNINDEXED,"
                    " target_type UNINDEXED,"
                    " target_id UNINDEXED,"
                    " title,"
                    " content,"
                    " tokenize = 'unicode61 remove_diacritics 1'"
                    ")"
                )
            )
        self._schema_ready = True

    async def upsert(self, records: list[TextSearchRecord]) -> None:
        if not records:
            return
        await self._ensure_schema()
        async with self._engine.begin() as conn:
            for r in records:
                # FTS5 не умеет UPSERT — удаляем по id и вставляем заново.
                await conn.execute(text("DELETE FROM text_search WHERE id = :id"), {"id": r.id})
                await conn.execute(
                    text(
                        "INSERT INTO text_search (id, target_type, target_id, title, content) "
                        "VALUES (:id, :tt, :tid, :title, :content)"
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
            placeholders = ", ".join(f":id{i}" for i in range(len(target_ids)))
            params: dict[str, str] = {f"id{i}": tid for i, tid in enumerate(target_ids)}
            params["tt"] = target_type
            res = await conn.execute(
                text(
                    f"DELETE FROM text_search "
                    f"WHERE target_type = :tt AND target_id IN ({placeholders})"
                ),
                params,
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
        cleaned = _sanitize_query(query)
        if not cleaned:
            return []
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, target_type, target_id, title, content, "
                        "       bm25(text_search) AS rank "
                        "FROM text_search "
                        "WHERE text_search MATCH :q "
                        "ORDER BY rank "
                        "LIMIT :k"
                    ),
                    {"q": cleaned, "k": top_k * 3},
                )
            ).mappings().all()

        results: list[TextSearchHit] = []
        for row in rows:
            tt = row["target_type"]
            if target_types and tt not in target_types:
                continue
            rank = float(row["rank"])
            # BM25: меньше — лучше; инвертируем в положительный score.
            score = 1.0 / (1.0 + rank) if rank >= 0 else float("inf")
            results.append(
                TextSearchHit(
                    id=row["id"],
                    target_type=tt,
                    target_id=row["target_id"],
                    title=row["title"],
                    content=row["content"],
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
                    text("SELECT COUNT(*) FROM text_search WHERE target_type = :tt"),
                    {"tt": target_type},
                )
            else:
                row = await conn.execute(text("SELECT COUNT(*) FROM text_search"))
            return int(row.scalar() or 0)
