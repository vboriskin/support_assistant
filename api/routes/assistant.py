"""Assistant: chat + streaming SSE + analyze (combined)."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.dependencies import assistant_service, categorizer_service, get_user_id
from api.schemas import AssistantChatRequest
from core.models import Answer, AssistantRequest, TicketContext
from services.assistant import AssistantService
from services.categorizer import CategorizationResult, CategorizeRequest, CategorizerService

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _to_assistant_request(body: AssistantChatRequest) -> AssistantRequest:
    return AssistantRequest(
        query=body.query,
        conversation_id=body.conversation_id,
        ticket_context=body.ticket_context,
        filters=body.filters,
        allow_clarify=body.allow_clarify,
    )


@router.post("/chat", response_model=Answer)
async def chat(
    body: AssistantChatRequest,
    _user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[AssistantService, Depends(assistant_service)],
) -> Answer:
    return await service.answer(_to_assistant_request(body))


@router.post("/chat/stream")
async def chat_stream(
    body: AssistantChatRequest,
    _user_id: Annotated[str, Depends(get_user_id)],
    service: Annotated[AssistantService, Depends(assistant_service)],
) -> StreamingResponse:
    request = _to_assistant_request(body)
    request.stream = True

    async def event_generator():
        try:
            async for chunk in service.answer_stream(request):
                data = chunk.model_dump_json(exclude_none=True)
                yield f"data: {data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:  # noqa: BLE001
            err = json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)
            yield f"data: {err}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ----------------------------------------------------------------------
# POST /api/assistant/analyze — категоризация + RAG-ответ + draft в одном
# ----------------------------------------------------------------------


_DRAFT_RE = re.compile(
    r"===\s*Драфт ответа клиенту\s*===\s*\n(.*?)(?:\n===|\Z)",
    re.IGNORECASE | re.DOTALL,
)


class AnalyzeRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=4000)
    description: str = Field(..., min_length=1, max_length=20000)
    channel: str | None = None
    author_role: str | None = None


class AnalyzeResponse(BaseModel):
    categorization: CategorizationResult
    answer: Answer
    suggested_response_to_user: str = ""


def _extract_draft(text: str) -> str:
    m = _DRAFT_RE.search(text or "")
    return m.group(1).strip() if m else ""


@router.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    _user_id: Annotated[str, Depends(get_user_id)],
    assistant: Annotated[AssistantService, Depends(assistant_service)],
    cat: Annotated[CategorizerService, Depends(categorizer_service)],
) -> dict[str, Any]:
    """Один вызов: категоризация → RAG-ответ с контекстом тикета → draft.

    Сценарий: оператор открыл карточку тикета, нажал «анализировать», получил
    модуль + ответ + готовый драфт письма за один HTTP-запрос.
    """
    cat_result = await cat.categorize(
        CategorizeRequest(
            subject=body.subject,
            description=body.description,
            channel=body.channel,
            author_role=body.author_role,
        )
    )

    # Запрос для ассистента строим как «расскажи, как решить эту проблему».
    # Передаём ticket_context, чтобы retrieval поднял релевантные источники.
    query = body.subject
    ticket_ctx = TicketContext(
        subject=body.subject,
        description=body.description,
        module=cat_result.categorization.module,
        category=cat_result.categorization.category,
    )
    answer = await assistant.answer(
        AssistantRequest(query=query, ticket_context=ticket_ctx)
    )
    draft = _extract_draft(answer.text)

    return {
        "categorization": cat_result.model_dump(mode="json"),
        "answer": answer.model_dump(mode="json"),
        "suggested_response_to_user": draft,
    }
