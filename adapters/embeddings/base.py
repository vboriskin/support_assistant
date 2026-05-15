"""Базовый интерфейс embeddings-адаптера.

Документная и поисковая нагрузки идут через разные методы — у моделей семейства
``multilingual-e5`` это критично, они различают document/query через префиксы.
Отдельные методы помогают не забыть про это в вызывающем коде.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingsClient(Protocol):
    @property
    def dimension(self) -> int: ...

    @property
    def model_name(self) -> str: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги для документов индекса (с document-префиксом, если нужен)."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Эмбеддинг для поискового запроса (с query-префиксом, если нужен)."""
        ...

    async def aclose(self) -> None: ...
