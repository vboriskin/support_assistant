"""In-memory ``VectorStore`` для тестов.

Замещает ``sqlite-vec`` на macOS python.org-сборке, где ``load_extension``
недоступен. Cosine similarity считается на нормализованных векторах
(``MockEmbeddingsClient`` всегда возвращает их таковыми).
"""

from __future__ import annotations

from typing import Any

from adapters.vector_store.base import VectorRecord, VectorSearchHit


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}

    async def upsert(self, records: list[VectorRecord]) -> None:
        for r in records:
            self._records[r.id] = r

    async def delete_by_target(self, target_type: str, target_ids: list[str]) -> int:
        to_remove = [
            r.id
            for r in self._records.values()
            if r.target_type == target_type and r.target_id in target_ids
        ]
        for rid in to_remove:
            del self._records[rid]
        return len(to_remove)

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 30,
        target_types: list[str] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[VectorSearchHit]:
        candidates = []
        for r in self._records.values():
            if target_types and r.target_type not in target_types:
                continue
            if metadata_filters and not all(
                r.metadata.get(k) == v for k, v in metadata_filters.items()
            ):
                continue
            score = sum(a * b for a, b in zip(r.vector, query_vector, strict=True))
            if score < min_score:
                continue
            candidates.append((score, r))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [
            VectorSearchHit(
                id=r.id,
                target_type=r.target_type,
                target_id=r.target_id,
                text=r.text,
                metadata=r.metadata,
                score=float(s),
            )
            for s, r in candidates[:top_k]
        ]

    async def count(self, target_type: str | None = None) -> int:
        if target_type is None:
            return len(self._records)
        return sum(1 for r in self._records.values() if r.target_type == target_type)

    async def health(self) -> bool:
        return True
