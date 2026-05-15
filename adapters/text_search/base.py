"""Базовый интерфейс полнотекстового поиска.

Структурно повторяет ``VectorStore``: upsert / delete_by_target / search. Score
зависит от движка (BM25 в SQLite FTS5, ts_rank в Postgres) — нормализация
делается на уровне retrieval (этап 8) через RRF.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class TextSearchRecord(BaseModel):
    id: str
    target_type: str
    target_id: str
    title: str
    content: str


class TextSearchHit(BaseModel):
    id: str
    target_type: str
    target_id: str
    title: str
    content: str
    score: float


@runtime_checkable
class TextSearch(Protocol):
    async def upsert(self, records: list[TextSearchRecord]) -> None: ...

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int: ...

    async def search(
        self,
        query: str,
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
    ) -> list[TextSearchHit]: ...

    async def count(self, target_type: str | None = None) -> int: ...
