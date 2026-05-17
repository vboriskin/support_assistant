"""LLM-as-reranker.

Передаём в LLM запрос и список сниппетов, получаем обратно индексы наиболее
релевантных. Если ответ модели не распарсился — graceful fallback на топ-K из
RRF, как просит ``docs/10-RETRIEVAL.md``.
"""

from __future__ import annotations

import json
from typing import Any

from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from config.logging import get_logger
from config.settings import Settings
from core.prompts.loader import load_prompt
from pipelines.ticket_ingestion._json import extract_json_object

logger = get_logger("services.reranker")

_SNIPPET_PREVIEW_CHARS = 400


def _format_snippet(idx: int, candidate: dict[str, Any]) -> str:
    title = candidate.get("title") or ""
    text = candidate.get("text") or ""
    body = text[:_SNIPPET_PREVIEW_CHARS]
    head = f"[{idx}]"
    if title:
        head += f" {title}"
    return f"{head}\n{body}"


class LLMReranker:
    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        if len(candidates) <= top_k:
            return candidates

        template = load_prompt("reranker")
        snippets_block = "\n\n".join(
            _format_snippet(i, c) for i, c in enumerate(candidates)
        )
        user_prompt = template.format(query=query, snippets=snippets_block, top_k=top_k)

        try:
            response = await self.llm.chat_completion(
                messages=[
                    ChatMessage(
                        role="system",
                        content="Ты — переранжировщик источников. Отвечай только JSON.",
                    ),
                    ChatMessage(role="user", content=user_prompt),
                ],
                temperature=0.0,
                max_tokens=300,
                json_mode=True,
            )
        except LLMError as e:
            logger.warning("reranker.llm_error", error=str(e))
            return candidates[:top_k]

        try:
            payload = json.loads(extract_json_object(response.text))
            raw_indices = payload.get("indices", [])
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.warning("reranker.parse_error", error=str(e), raw=response.text[:200])
            return candidates[:top_k]

        valid: list[int] = []
        seen: set[int] = set()
        for raw in raw_indices:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(candidates) and idx not in seen:
                valid.append(idx)
                seen.add(idx)
            if len(valid) >= top_k:
                break

        if not valid:
            return candidates[:top_k]

        return [candidates[i] for i in valid]


class NoopReranker:
    """Заглушка для ``RERANKER_TYPE=none``."""

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        return candidates[:top_k]


class CrossEncoderReranker:
    """Локальный cross-encoder (sentence-transformers).

    Default-модель: ``BAAI/bge-reranker-v2-m3`` (или из ``RERANKER_MODEL``).
    Латентность 50–100 мс на CPU для 15 кандидатов — гораздо быстрее
    LLM-reranker'а. Минус: ещё одна модель в памяти (~600 МБ).

    Lazy-init: модель загружается при первом ``rerank``-вызове, чтобы не
    блокировать старт приложения.
    """

    DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model_name = settings.reranker.model or self.DEFAULT_MODEL
        self._model = None  # type: ignore[assignment]
        self._lock = None

    async def _ensure_model(self):  # type: ignore[no-untyped-def]
        if self._model is not None:
            return self._model
        import asyncio as _asyncio

        if self._lock is None:
            self._lock = _asyncio.Lock()
        async with self._lock:
            if self._model is not None:
                return self._model
            logger.info("reranker.cross_encoder.loading", model=self._model_name)
            from sentence_transformers import CrossEncoder

            loop = _asyncio.get_running_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: CrossEncoder(
                    self._model_name,
                    cache_folder=str(self.settings.embeddings.cache_dir),
                ),
            )
            logger.info("reranker.cross_encoder.loaded")
            return self._model

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        if len(candidates) <= top_k:
            return candidates
        try:
            model = await self._ensure_model()
        except Exception as e:
            logger.warning("reranker.cross_encoder.load_failed", error=str(e))
            return candidates[:top_k]
        import asyncio as _asyncio

        pairs = [(query, c.get("text", "") or "") for c in candidates]
        loop = _asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None, lambda: model.predict(pairs, show_progress_bar=False)
        )
        scored = sorted(
            zip(candidates, scores, strict=True), key=lambda x: float(x[1]), reverse=True
        )
        return [c for c, _ in scored[:top_k]]


def create_reranker(llm: LLMClient, settings: Settings):
    rtype = settings.reranker.type
    if not settings.reranker.enabled or rtype == "none":
        return NoopReranker()
    if rtype == "llm":
        return LLMReranker(llm, settings)
    if rtype == "cross_encoder":
        return CrossEncoderReranker(settings)
    # cross_encoder появится позднее — пока деградируем в noop
    logger.warning("reranker.unsupported_type", type=rtype, fallback="noop")
    return NoopReranker()
