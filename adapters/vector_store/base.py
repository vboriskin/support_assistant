"""Базовый интерфейс векторного хранилища.

Хранилище — это абстракция над таблицей эмбеддингов. Поддерживает upsert,
векторный поиск (KNN), фильтры по типу записи и по метаданным, удаление и
подсчёт. Метрика — cosine similarity ``[0..1]`` (нормализованные векторы).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class VectorRecord(BaseModel):
    """Запись индексируемого объекта вместе с её эмбеддингом."""

    id: str
    target_type: str
    target_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    vector: list[float]


class VectorSearchHit(BaseModel):
    """Результат поиска."""

    id: str
    target_type: str
    target_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(self, records: list[VectorRecord]) -> None: ...

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int: ...

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]: ...

    async def count(self, target_type: str | None = None) -> int: ...

    async def health(self) -> bool: ...
