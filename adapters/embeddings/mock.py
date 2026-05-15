"""Детерминистический mock эмбеддингов.

Используется в тестах и при ``EMBEDDINGS_PROVIDER=mock``. Алгоритм:
``md5(text)`` → seed → нормализованный вектор из стандартного нормального
распределения. Это даёт:

- **детерминизм**: один и тот же текст всегда отображается в один и тот же вектор;
- **различимость**: разные тексты с очень высокой вероятностью получают разные векторы;
- **косинусную нормировку**: ``||v|| == 1`` — как и у настоящей модели с
  ``normalize_embeddings=True``.
"""

from __future__ import annotations

import hashlib

import numpy as np

from config.settings import Settings


class MockEmbeddingsClient:
    model_id = "mock-embeddings"

    def __init__(self, settings: Settings | None = None, *, dimension: int | None = None) -> None:
        if dimension is not None:
            self._dimension = dimension
        elif settings is not None:
            self._dimension = settings.embeddings.dimension
        else:
            self._dimension = 1024

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self.model_id

    def _vector(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.md5(text.encode("utf-8")).digest()[:4], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self._dimension).astype(np.float32)
        norm = float(np.linalg.norm(v))
        if norm == 0.0:
            # практически невозможно, но защитимся
            v[0] = 1.0
            norm = 1.0
        v = v / norm
        return v.tolist()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    async def aclose(self) -> None:
        return None
