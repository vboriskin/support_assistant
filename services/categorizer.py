"""Автокатегоризация входящего обращения.

Поток:

1. До PII-маскирования: достаём ``application_id`` из исходного текста (он
   нужен оператору в исходном виде).
2. Маскируем ``subject`` / ``description`` через ``PIIMaskingPipeline``.
3. LLM-вызов с замаскированным контентом. Битый JSON → fallback на
   ``other``/``normal``/``confidence=0``.
4. Опционально ищем похожие открытые тикеты в vector_store, фильтруем по
   ``status in (open, in_progress)`` через ``TicketsRepository``.

Никакого fancy в результате — единичный LLM-запрос и единичный векторный.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from adapters.embeddings.base import EmbeddingsClient
from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from adapters.vector_store.base import VectorStore
from config.logging import get_logger
from config.settings import Settings
from core.models import Categorization
from core.pii.pipeline import PIIMaskingPipeline
from core.prompts.loader import load_prompt
from pipelines.ticket_ingestion._json import extract_json_object

logger = get_logger("services.categorizer")


DEFAULT_MODULES = (
    "Скоринг",
    "Документы",
    "Андеррайтинг",
    "Решение",
    "Подписание",
    "Интеграции",
    "Общее",
)

_APPLICATION_ID_RE = re.compile(r"\b(?:APP|ЗПК|КЗ|ЗС)[\-_]?\d{4,}\b")
_VALID_TYPES = {
    "bug",
    "question",
    "access_request",
    "feature_request",
    "incident",
    "duplicate",
    "other",
}
_VALID_URGENCIES = {"low", "normal", "high", "critical"}


class _TicketsRepoProto(Protocol):
    async def get(self, id: str) -> Any: ...


class CategorizeRequest(BaseModel):
    subject: str
    description: str
    channel: str | None = None
    author_role: str | None = None
    attachments: list[str] = Field(default_factory=list)


class SimilarTicket(BaseModel):
    ticket_id: str
    external_id: str
    subject: str
    status: str
    score: float


class CategorizationResult(BaseModel):
    categorization: Categorization
    similar_open_tickets: list[SimilarTicket] = Field(default_factory=list)
    latency_ms: int


def extract_application_id(text: str) -> str | None:
    """Грубое извлечение ID заявки из исходного текста (до маскирования)."""
    m = _APPLICATION_ID_RE.search(text)
    return m.group(0) if m else None


class CategorizerService:
    def __init__(
        self,
        *,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        vector_store: VectorStore,
        tickets_repo: _TicketsRepoProto,
        pii: PIIMaskingPipeline,
        settings: Settings,
        modules: tuple[str, ...] = DEFAULT_MODULES,
    ) -> None:
        self.llm = llm
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.tickets_repo = tickets_repo
        self.pii = pii
        self.settings = settings
        self.modules = modules

    async def categorize(self, request: CategorizeRequest) -> CategorizationResult:
        t0 = time.time()

        # 1. Application ID — из НЕзамаскированного текста.
        app_id = extract_application_id(f"{request.subject}\n{request.description}")

        # 2. Маскирование PII перед LLM.
        masked_subject = self.pii.mask(request.subject).masked_text
        masked_description = self.pii.mask(request.description).masked_text

        # 3. LLM-категоризация.
        cat = await self._llm_categorize(masked_subject, masked_description, request)
        cat.extracted_application_id = app_id

        # 4. Похожие открытые тикеты.
        similar = await self._find_similar_open(
            f"{masked_subject}\n{masked_description}"
        )

        return CategorizationResult(
            categorization=cat,
            similar_open_tickets=similar,
            latency_ms=int((time.time() - t0) * 1000),
        )

    async def _llm_categorize(
        self,
        subject: str,
        description: str,
        request: CategorizeRequest,
    ) -> Categorization:
        template = load_prompt("categorization")
        user_prompt = template.format(
            subject=subject,
            description=description,
            channel=request.channel or "(не указан)",
            author_role=request.author_role or "(не указана)",
            modules=", ".join(self.modules),
        )
        try:
            response = await self.llm.chat_completion(
                messages=[
                    ChatMessage(
                        role="system",
                        content="Ты — классификатор обращений в техподдержку. Отвечай только JSON.",
                    ),
                    ChatMessage(role="user", content=user_prompt),
                ],
                temperature=0.1,
                max_tokens=400,
                json_mode=True,
            )
        except LLMError as e:
            logger.warning("categorizer.llm_error", error=str(e))
            return self._fallback(f"llm_error: {e}")

        raw = extract_json_object(response.text)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "categorizer.parse_error", error=str(e), raw=raw[:200]
            )
            return self._fallback(f"parse_error: {e}")

        try:
            type_ = data.get("type", "other")
            if type_ not in _VALID_TYPES:
                type_ = "other"
            urgency = data.get("urgency", "normal")
            if urgency not in _VALID_URGENCIES:
                urgency = "normal"
            return Categorization(
                category=str(data.get("category") or "Общее").strip()[:80] or "Общее",
                module=data.get("module") or None,
                type=type_,
                urgency=urgency,
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                suggested_assignee_group=data.get("suggested_assignee_group") or None,
                reasoning=str(data.get("reasoning") or ""),
            )
        except (ValidationError, ValueError, TypeError) as e:
            logger.warning("categorizer.validation_error", error=str(e))
            return self._fallback(str(e))

    @staticmethod
    def _fallback(reasoning: str) -> Categorization:
        return Categorization(
            category="Общее",
            module=None,
            type="other",
            urgency="normal",
            confidence=0.0,
            suggested_assignee_group=None,
            reasoning=reasoning,
        )

    async def _find_similar_open(self, text: str) -> list[SimilarTicket]:
        if not text.strip():
            return []
        try:
            vec = await self.embeddings.embed_query(text)
            hits = await self.vector_store.search(
                query_vector=vec,
                top_k=5,
                target_types=["ticket_summary", "ticket_symptom"],
                min_score=0.80,
            )
        except Exception as e:
            logger.warning("categorizer.similarity_search_failed", error=str(e))
            return []

        seen: set[str] = set()
        results: list[SimilarTicket] = []
        for hit in hits:
            if hit.target_id in seen:
                continue
            seen.add(hit.target_id)
            try:
                ticket = await self.tickets_repo.get(hit.target_id)
            except Exception as e:
                logger.warning("categorizer.ticket_lookup_failed", error=str(e))
                continue
            if ticket is None:
                continue
            status = getattr(ticket, "status", None)
            if status not in ("open", "in_progress"):
                continue
            results.append(
                SimilarTicket(
                    ticket_id=ticket.id,
                    external_id=ticket.external_id,
                    subject=ticket.subject,
                    status=status,
                    score=float(hit.score),
                )
            )
            if len(results) >= 3:
                break
        return results
