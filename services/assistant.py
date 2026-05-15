"""RAG-оркестратор.

Один экземпляр обслуживает оба режима — обычный ``answer()`` и SSE-стрим
``answer_stream()``. Логика общая (retrieval → prompt → LLM → форматтер),
различия только в выводе.

История диалога и логи LLM-вызовов опциональны: если соответствующие репо
не переданы — соответствующий шаг пропускается. Это позволяет использовать
ассистента в одиночных контекстах (CLI/eval-runner) без БД.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from config.logging import get_logger
from config.settings import Settings
from core.models import (
    Answer,
    AssistantChunk,
    AssistantRequest,
)
from services.answer_formatter import AnswerFormatter
from services.prompt_builder import PromptBuilder
from services.retrieval import RetrievalFilters, RetrievalService

logger = get_logger("services.assistant")


class _ConversationsRepoProto(Protocol):
    async def add_message(self, *, conversation_id: str, role: str, content: str, **kw: Any) -> Any: ...


class _LLMLogsRepoProto(Protocol):
    async def record(
        self,
        *,
        purpose: str,
        model: str,
        prompt: str,
        response: str | None,
        latency_ms: int,
        **kw: Any,
    ) -> Any: ...


_CLARIFY_RE = re.compile(r"<clarify>(.*?)</clarify>", re.IGNORECASE | re.DOTALL)


def _extract_clarify(text: str) -> str | None:
    m = _CLARIFY_RE.search(text or "")
    if not m:
        return None
    q = m.group(1).strip()
    return q or None


_NO_SOURCES_TEXT = (
    "В базе знаний и истории закрытых тикетов нет информации по вашему запросу.\n\n"
    "Возможные дальнейшие действия:\n"
    "- Уточните формулировку (укажите модуль, конкретный симптом, текст ошибки).\n"
    "- Если это новая проблема — заведите тикет на 2-ю линию.\n"
    "- Если у вас есть решение — добавьте его в KB, чтобы оно стало доступно команде."
)


class AssistantService:
    def __init__(
        self,
        *,
        retrieval: RetrievalService,
        llm: LLMClient,
        prompt_builder: PromptBuilder,
        formatter: AnswerFormatter,
        settings: Settings,
        conversations_repo: _ConversationsRepoProto | None = None,
        llm_logs_repo: _LLMLogsRepoProto | None = None,
    ) -> None:
        self.retrieval = retrieval
        self.llm = llm
        self.prompt_builder = prompt_builder
        self.formatter = formatter
        self.settings = settings
        self.conv_repo = conversations_repo
        self.logs_repo = llm_logs_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def answer(self, request: AssistantRequest) -> Answer:
        request_id = str(uuid.uuid4())
        t0 = time.time()
        effective_query = self._expand_query(request)
        filters = self._build_filters(request)

        retrieval_result = await self.retrieval.search(effective_query, filters=filters)
        if not retrieval_result.sources:
            return Answer(
                text=_NO_SOURCES_TEXT,
                citations=[],
                used_sources=[],
                model_used=self.llm.model_name,
                latency_ms=int((time.time() - t0) * 1000),
                conversation_id=request.conversation_id,
            )

        messages = self.prompt_builder.build(
            query=request.query,
            sources=retrieval_result.sources,
            ticket_context=request.ticket_context,
            history=None,  # история диалога — позднее, на этапе API
            allow_clarify=request.allow_clarify,
        )

        try:
            llm_response = await self.llm.chat_completion(
                messages=messages,
                temperature=0.2,
                max_tokens=1500,
                request_id=request_id,
            )
        except LLMError as e:
            await self._log_llm(
                purpose="answer",
                messages=messages,
                response_text=None,
                response_tokens=None,
                latency_ms=int((time.time() - t0) * 1000),
                error=str(e),
            )
            raise

        latency_ms = int((time.time() - t0) * 1000)
        answer = self.formatter.parse(
            text=llm_response.text,
            used_sources=retrieval_result.sources,
            model=self.llm.model_name,
            latency_ms=latency_ms,
            token_usage={
                "prompt": llm_response.prompt_tokens,
                "completion": llm_response.completion_tokens,
                "total": llm_response.total_tokens,
            },
            conversation_id=request.conversation_id,
        )

        if request.allow_clarify:
            answer.clarify_question = _extract_clarify(answer.text)
        answer.message_id = await self._persist(request, answer)
        await self._log_llm(
            purpose="answer",
            messages=messages,
            response_text=llm_response.text,
            response_tokens=(llm_response.prompt_tokens, llm_response.completion_tokens),
            latency_ms=latency_ms,
        )
        return answer

    async def answer_stream(
        self, request: AssistantRequest
    ) -> AsyncIterator[AssistantChunk]:
        request_id = str(uuid.uuid4())
        t0 = time.time()
        effective_query = self._expand_query(request)
        filters = self._build_filters(request)
        retrieval_result = await self.retrieval.search(effective_query, filters=filters)

        # Первый чанк — источники: UI показывает их параллельно с генерацией.
        yield AssistantChunk(
            type="sources",
            sources=retrieval_result.sources,
            request_id=request_id,
        )

        if not retrieval_result.sources:
            yield AssistantChunk(
                type="final",
                answer=Answer(
                    text=_NO_SOURCES_TEXT,
                    citations=[],
                    used_sources=[],
                    model_used=self.llm.model_name,
                    latency_ms=int((time.time() - t0) * 1000),
                    conversation_id=request.conversation_id,
                ),
                request_id=request_id,
            )
            return

        messages = self.prompt_builder.build(
            query=request.query,
            sources=retrieval_result.sources,
            ticket_context=request.ticket_context,
            allow_clarify=request.allow_clarify,
        )

        full_text = ""
        try:
            async for chunk in self.llm.chat_completion_stream(
                messages=messages,
                temperature=0.2,
                max_tokens=1500,
                request_id=request_id,
            ):
                if chunk.delta_text:
                    full_text += chunk.delta_text
                    yield AssistantChunk(
                        type="delta",
                        delta=chunk.delta_text,
                        request_id=request_id,
                    )
        except LLMError as e:
            yield AssistantChunk(type="error", error=str(e), request_id=request_id)
            await self._log_llm(
                purpose="answer",
                messages=messages,
                response_text=full_text or None,
                response_tokens=None,
                latency_ms=int((time.time() - t0) * 1000),
                error=str(e),
            )
            return

        latency_ms = int((time.time() - t0) * 1000)
        answer = self.formatter.parse(
            text=full_text,
            used_sources=retrieval_result.sources,
            model=self.llm.model_name,
            latency_ms=latency_ms,
            conversation_id=request.conversation_id,
        )
        if request.allow_clarify:
            answer.clarify_question = _extract_clarify(answer.text)
        answer.message_id = await self._persist(request, answer)
        yield AssistantChunk(type="final", answer=answer, request_id=request_id)

        await self._log_llm(
            purpose="answer",
            messages=messages,
            response_text=full_text,
            response_tokens=None,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Внутренние помощники
    # ------------------------------------------------------------------

    @staticmethod
    def _expand_query(request: AssistantRequest) -> str:
        if not request.ticket_context:
            return request.query
        ctx = request.ticket_context
        parts = [request.query]
        if ctx.subject:
            parts.append(ctx.subject)
        if ctx.description:
            parts.append(ctx.description[:200])
        return " ".join(parts)

    @staticmethod
    def _build_filters(request: AssistantRequest) -> RetrievalFilters:
        if not request.filters:
            return RetrievalFilters()
        return RetrievalFilters(**request.filters)

    async def _persist(self, request: AssistantRequest, answer: Answer) -> str | None:
        if not self.conv_repo or not request.conversation_id:
            return None
        try:
            await self.conv_repo.add_message(
                conversation_id=request.conversation_id,
                role="user",
                content=request.query,
            )
            msg = await self.conv_repo.add_message(
                conversation_id=request.conversation_id,
                role="assistant",
                content=answer.text,
                citations=[c.model_dump(mode="json") for c in answer.citations],
                used_sources=[s.model_dump(mode="json") for s in answer.used_sources],
            )
            session = getattr(self.conv_repo, "session", None)
            if session is not None:
                await session.commit()
            return getattr(msg, "id", None)
        except Exception as e:  # noqa: BLE001 — лог сохранения не должен валить ответ
            logger.warning("assistant.persist_failed", error=str(e))
            return None

    async def _log_llm(
        self,
        *,
        purpose: str,
        messages: list[ChatMessage],
        response_text: str | None,
        response_tokens: tuple[int | None, int | None] | None,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        if self.logs_repo is None:
            return
        full_prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
        prompt_tokens = response_tokens[0] if response_tokens else None
        completion_tokens = response_tokens[1] if response_tokens else None
        try:
            await self.logs_repo.record(
                purpose=purpose,
                model=self.llm.model_name,
                prompt=full_prompt,
                response=response_text,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("assistant.log_failed", error=str(e))


# ----------------------------------------------------------------------
# Хелпер для unit-тестов: сборка ассистента из частей
# ----------------------------------------------------------------------


def build_assistant(
    *,
    settings: Settings,
    retrieval: RetrievalService,
    llm: LLMClient,
    conversations_repo: _ConversationsRepoProto | None = None,
    llm_logs_repo: _LLMLogsRepoProto | None = None,
) -> AssistantService:
    return AssistantService(
        retrieval=retrieval,
        llm=llm,
        prompt_builder=PromptBuilder(settings),
        formatter=AnswerFormatter(),
        settings=settings,
        conversations_repo=conversations_repo,
        llm_logs_repo=llm_logs_repo,
    )
