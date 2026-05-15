"""Локальные эмбеддинги через ``sentence-transformers``.

Поведение:

- **Lazy-init.** Веса грузятся при первом вызове ``embed_*``. Это медленно
  (5–15 секунд для multilingual-e5-large на CPU). Под защитой ``asyncio.Lock``,
  чтобы параллельные вызовы не загружали модель дважды.
- **Префиксы для multilingual-e5.** ``passage: ``/``query: `` — без них качество
  падает заметно. Если в будущем подключим другую модель — префиксы можно
  переопределить через override-механизм (пока — константы).
- **Нормализация.** ``normalize_embeddings=True`` — cosine = dot product, что
  упрощает работу vector-store.
- **Async-обёртка над синхронным ``encode``.** Тяжёлый CPU-bound вызов идёт
  в default-executor, чтобы не блокировать event loop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from config.logging import get_logger
from config.settings import Settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = get_logger("adapters.embeddings.local_st")


class LocalSentenceTransformersClient:
    """Локальные эмбеддинги."""

    _DOC_PREFIX = "passage: "
    _QUERY_PREFIX = "query: "

    def __init__(self, settings: Settings) -> None:
        s = settings.embeddings
        self._model_name = s.model_name
        self._cache_dir = s.cache_dir
        self._device = s.device
        self._batch_size = s.batch_size
        self._dimension = s.dimension
        self._model: SentenceTransformer | None = None
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def _ensure_model(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            logger.info(
                "embeddings.loading",
                model=self._model_name,
                device=self._device,
                cache_dir=str(self._cache_dir),
            )
            from sentence_transformers import SentenceTransformer

            loop = asyncio.get_running_loop()
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            model = await loop.run_in_executor(
                None,
                lambda: SentenceTransformer(
                    self._model_name,
                    cache_folder=str(self._cache_dir),
                    device=self._device,
                ),
            )
            actual_dim = int(model.get_sentence_embedding_dimension() or 0)
            if actual_dim and actual_dim != self._dimension:
                logger.warning(
                    "embeddings.dimension_mismatch",
                    expected=self._dimension,
                    actual=actual_dim,
                    model=self._model_name,
                )
                self._dimension = actual_dim
            logger.info("embeddings.loaded", dimension=self._dimension)
            self._model = model
            return model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._ensure_model()
        prefixed = [self._DOC_PREFIX + t for t in texts]
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(
            None,
            lambda: model.encode(
                prefixed,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
        )
        return [v.tolist() for v in vectors]

    async def embed_query(self, text: str) -> list[float]:
        model = await self._ensure_model()
        loop = asyncio.get_running_loop()
        vector = await loop.run_in_executor(
            None,
            lambda: model.encode(
                [self._QUERY_PREFIX + text],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0],
        )
        return vector.tolist()

    async def aclose(self) -> None:
        if self._model is None:
            return
        self._model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
