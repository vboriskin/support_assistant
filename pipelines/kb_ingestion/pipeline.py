"""Композиция KB-ингеста: loader → chunking → index."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.embeddings.base import EmbeddingsClient
from adapters.text_search.base import TextSearch
from adapters.vector_store.base import VectorStore
from config.logging import get_logger
from core.chunking import chunk_text

from .html_loader import iter_html_files
from .index import index_article
from .markdown_loader import RawArticle, iter_markdown_files

logger = get_logger("pipelines.kb_ingestion")


class KBIngestionPipeline:
    def __init__(
        self,
        *,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        text_search: TextSearch,
        session_factory: async_sessionmaker[AsyncSession],
        default_module: str | None = None,
        max_chars: int = 1200,
        overlap_chars: int = 100,
        min_chars: int = 80,
    ) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.text_search = text_search
        self.session_factory = session_factory
        self.default_module = default_module
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.min_chars = min_chars

    async def run(self, source_path: str, *, kind: str = "markdown") -> dict[str, Any]:
        """Прогон по директории с файлами KB. ``kind`` ∈ {markdown, html}."""
        if kind == "markdown":
            articles: Iterable[RawArticle] = iter_markdown_files(source_path)
        elif kind == "html":
            articles = iter_html_files(source_path)
        else:
            raise ValueError(f"Unknown KB ingest kind: {kind}")

        # Warm-up индексов
        try:
            await self.vector_store.count()
            await self.text_search.count()
        except Exception as e:  # noqa: BLE001
            logger.warning("kb.warmup_failed", error=str(e))

        stats = {"total": 0, "indexed": 0, "skipped": 0, "failed": 0, "chunks": 0}
        for raw in articles:
            stats["total"] += 1
            chunks = chunk_text(
                raw.body,
                max_chars=self.max_chars,
                min_chars=self.min_chars,
                overlap_chars=self.overlap_chars,
            )
            if not chunks:
                stats["skipped"] += 1
                continue
            try:
                await index_article(
                    title=raw.title,
                    body=raw.body,
                    chunks=chunks,
                    embeddings=self.embeddings,
                    vector_store=self.vector_store,
                    text_search=self.text_search,
                    session_factory=self.session_factory,
                    module=self.default_module,
                    source_path=raw.source_path,
                )
                stats["indexed"] += 1
                stats["chunks"] += len(chunks)
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "kb.article_failed",
                    source=raw.source_path,
                    error=str(e),
                )
                stats["failed"] += 1
        return stats
