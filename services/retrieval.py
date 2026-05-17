"""Гибридный поиск с Reciprocal Rank Fusion (RRF).

Поток: параллельно бьём vector и FTS-индексы → объединяем ranking через RRF →
постфильтруем по необязательным условиям → опционально прогоняем через
reranker → отдаём список ``Source``.

RRF (см. ``docs/10-RETRIEVAL.md`` §"RRF"): для документа, попавшего в
позиции ``r_v`` (vector) и ``r_t`` (text), итоговый score равен
``1/(k+r_v) + 1/(k+r_t)``. Не требует калибровки шкал — главный плюс.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from adapters.embeddings.base import EmbeddingsClient
from adapters.text_search.base import TextSearch, TextSearchHit
from adapters.vector_store.base import VectorSearchHit, VectorStore
from config.logging import get_logger
from config.settings import Settings
from core.models import Source

logger = get_logger("services.retrieval")


class _Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 8,
    ) -> list[dict[str, Any]]: ...


class RetrievalFilters(BaseModel):
    target_types: list[str] | None = None
    modules: list[str] | None = None
    categories: list[str] | None = None
    min_score: float = 0.0
    only_known_issues: bool = False
    date_from: str | None = None
    date_to: str | None = None


class RetrievalResult(BaseModel):
    sources: list[Source] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _FusedCandidate:
    target_type: str
    target_id: str
    text: str
    metadata: dict[str, Any]
    vector_score: float = 0.0
    text_score: float = 0.0
    vector_rank: int | None = None
    text_rank: int | None = None
    rrf_score: float = 0.0
    title: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_id": self.target_id,
            "text": self.text,
            "metadata": self.metadata,
            "vector_score": self.vector_score,
            "text_score": self.text_score,
            "vector_rank": self.vector_rank,
            "text_rank": self.text_rank,
            "rrf_score": self.rrf_score,
            "title": self.title,
        }


def _key(target_type: str, target_id: str) -> str:
    return f"{target_type}:{target_id}"


def _build_title(c: dict[str, Any]) -> str:
    tt = c.get("target_type", "")
    text = c.get("text", "") or ""
    title = c.get("title")
    if title:
        return title
    if tt == "kb_chunk":
        return c.get("metadata", {}).get("article_title") or text[:80]
    if tt == "ticket_summary":
        return c.get("metadata", {}).get("summary_one_line") or text[:80]
    if tt == "ticket_symptom":
        return "Симптом: " + text[:80]
    return text[:80] or "Источник"


# FTS5 принимает фразу/слова, но не любые символы. Готовим OR-запрос только для
# SQLite-варианта (Postgres ``plainto_tsquery`` сам справляется).
_FTS_TOKEN_RE = re.compile(r"[\w\-]+", flags=re.UNICODE)


def sanitize_fts_query(q: str) -> str:
    tokens = [t for t in _FTS_TOKEN_RE.findall(q) if len(t) >= 2]
    if not tokens:
        return ""
    return " OR ".join(tokens)


def _rrf_merge(
    vector_hits: list[VectorSearchHit],
    text_hits: list[TextSearchHit],
    *,
    k: int = 60,
    keep: int = 30,
) -> list[_FusedCandidate]:
    fused: dict[str, _FusedCandidate] = {}

    for rank, hit in enumerate(vector_hits, start=1):
        key = _key(hit.target_type, hit.target_id)
        cand = fused.setdefault(
            key,
            _FusedCandidate(
                target_type=hit.target_type,
                target_id=hit.target_id,
                text=hit.text,
                metadata=dict(hit.metadata),
            ),
        )
        cand.vector_score = hit.score
        cand.vector_rank = rank
        cand.rrf_score += 1.0 / (k + rank)

    for rank, hit in enumerate(text_hits, start=1):
        key = _key(hit.target_type, hit.target_id)
        if key in fused:
            cand = fused[key]
            cand.text_score = hit.score
            cand.text_rank = rank
            if not cand.title:
                cand.title = hit.title
        else:
            cand = _FusedCandidate(
                target_type=hit.target_type,
                target_id=hit.target_id,
                text=hit.content,
                metadata={},
                title=hit.title,
                text_score=hit.score,
                text_rank=rank,
            )
            fused[key] = cand
        cand.rrf_score += 1.0 / (k + rank)

    return sorted(fused.values(), key=lambda c: c.rrf_score, reverse=True)[:keep]


class RetrievalService:
    def __init__(
        self,
        *,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        text_search: TextSearch,
        settings: Settings,
        reranker: _Reranker | None = None,
    ) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.text_search = text_search
        self.settings = settings
        self.reranker = reranker

    async def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        if not query.strip():
            return RetrievalResult(sources=[], debug={"reason": "empty_query"})

        filters = filters or RetrievalFilters()
        target_top_k = top_k or self.settings.retrieval.final_top_k

        # 1. Параллельно бьём в оба индекса
        vector_task = self._vector_search(query, filters)
        text_task = self._text_search(query, filters)
        vector_hits, text_hits = await asyncio.gather(vector_task, text_task)

        # 2. RRF
        fused = _rrf_merge(
            vector_hits,
            text_hits,
            k=self.settings.retrieval.rrf_k,
            keep=max(target_top_k * 2, 15),
        )

        # 3. Постфильтры (на уровне vector_store применяем только простые;
        # для multi-module/date — фильтруем здесь).
        if filters.modules and len(filters.modules) > 1:
            wanted = set(filters.modules)
            fused = [c for c in fused if c.metadata.get("module") in wanted]
        if filters.only_known_issues:
            fused = [c for c in fused if c.metadata.get("is_known_issue") is True]
        if filters.date_from:
            fused = [
                c for c in fused if (c.metadata.get("created_at") or "") >= filters.date_from
            ]
        if filters.date_to:
            fused = [
                c for c in fused if (c.metadata.get("created_at") or "") <= filters.date_to
            ]

        # 4. Reranker
        candidates = [c.as_dict() for c in fused]
        if self.reranker and self.settings.reranker.enabled and len(candidates) > target_top_k:
            try:
                reranked = await self.reranker.rerank(query, candidates, top_k=target_top_k)
            except Exception as e:
                logger.warning("reranker.failed", error=str(e))
                reranked = candidates[:target_top_k]
        else:
            reranked = candidates[:target_top_k]

        # 5. Source-объекты
        import math as _math

        def _clean(v: Any) -> float | None:
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            if _math.isnan(f) or _math.isinf(f):
                return None
            return f

        sources: list[Source] = []
        for rank, c in enumerate(reranked):
            v_rank = c.get("vector_rank")
            t_rank = c.get("text_rank")
            if v_rank and t_rank:
                rs = "both"
            elif v_rank:
                rs = "vector"
            elif t_rank:
                rs = "fts"
            else:
                rs = None
            sources.append(
                Source(
                    source_type=c["target_type"],
                    source_id=c["target_id"],
                    title=_build_title(c),
                    content=c["text"],
                    metadata=c["metadata"],
                    score=_clean(c.get("rrf_score")) or 0.0,
                    rank=rank,
                    vector_score=_clean(c.get("vector_score")) if v_rank else None,
                    text_score=_clean(c.get("text_score")) if t_rank else None,
                    vector_rank=v_rank,
                    text_rank=t_rank,
                    retrieval_source=rs,
                )
            )

        return RetrievalResult(
            sources=sources,
            debug={
                "vector_count": len(vector_hits),
                "text_count": len(text_hits),
                "fused_count": len(fused),
                "reranked_count": len(reranked),
            },
        )

    # ------------------------------------------------------------------

    async def _vector_search(
        self, query: str, filters: RetrievalFilters
    ) -> list[VectorSearchHit]:
        try:
            vec = await self.embeddings.embed_query(query)
        except Exception as e:
            logger.warning("retrieval.embed_failed", error=str(e))
            return []
        md_filters: dict[str, Any] | None = None
        if filters.modules and len(filters.modules) == 1:
            md_filters = {"module": filters.modules[0]}
        if filters.only_known_issues:
            md_filters = {**(md_filters or {}), "is_known_issue": True}
        try:
            return await self.vector_store.search(
                query_vector=vec,
                top_k=self.settings.vector_store.search_top_k,
                target_types=filters.target_types,
                metadata_filters=md_filters,
                min_score=filters.min_score,
            )
        except Exception as e:
            logger.warning("retrieval.vector_search_failed", error=str(e))
            return []

    async def _text_search(
        self, query: str, filters: RetrievalFilters
    ) -> list[TextSearchHit]:
        # Если у нас SQLite-FTS5, query чувствителен к символам — санитизируем.
        is_sqlite = self.settings.db.backend == "sqlite"
        q = sanitize_fts_query(query) if is_sqlite else query
        if not q:
            return []
        try:
            return await self.text_search.search(
                query=q,
                top_k=self.settings.vector_store.text_search_top_k,
                target_types=filters.target_types,
            )
        except Exception as e:
            logger.warning("retrieval.text_search_failed", error=str(e))
            return []
