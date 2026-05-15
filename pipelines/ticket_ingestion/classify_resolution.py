"""LLM-классификация: был ли тикет реально решён.

Промпт — ``core/prompts/ticket_resolution_classifier.txt``. Если ответ модели
не парсится — возвращаем ``unclear`` (без падения пайплайна).
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from config.logging import get_logger
from config.settings import Settings
from core.models import Ticket
from core.prompts.loader import load_prompt

from ._json import extract_json_object

logger = get_logger("pipelines.ticket_ingestion.classify_resolution")

ResolutionStatus = Literal["resolved", "no_resolution", "workaround", "unclear"]


class ResolutionVerdict(BaseModel):
    resolution_status: ResolutionStatus
    reason: str = ""


def _format_ticket(t: Ticket) -> str:
    lines = [
        f"Категория: {t.category or '(не указана)'}",
        f"Модуль: {t.module or '(не указан)'}",
        f"Статус: {t.status}",
        f"Тема: {t.subject}",
        f"Описание: {t.description}",
    ]
    if t.conversation:
        lines.append("Переписка:")
        for c in t.conversation:
            role = c.author_role or "?"
            lines.append(f"  [{role}] {c.content}")
    return "\n".join(lines)


async def classify_resolution(
    ticket: Ticket,
    llm: LLMClient,
    settings: Settings,
) -> ResolutionVerdict:
    template = load_prompt("ticket_resolution_classifier")
    user_prompt = template.format(ticket_text=_format_ticket(ticket))

    try:
        response = await llm.chat_completion(
            messages=[
                ChatMessage(
                    role="system",
                    content="Ты — классификатор тикетов поддержки. Отвечай строго в JSON.",
                ),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=0.0,
            max_tokens=200,
            json_mode=True,
        )
    except LLMError as e:
        logger.warning("classify.llm_error", external_id=ticket.external_id, error=str(e))
        return ResolutionVerdict(resolution_status="unclear", reason=f"llm_error: {e}")

    raw = extract_json_object(response.text)
    try:
        return ResolutionVerdict.model_validate_json(raw)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning(
            "classify.parse_error",
            external_id=ticket.external_id,
            error=str(e),
            raw=raw[:200],
        )
        return ResolutionVerdict(resolution_status="unclear", reason=f"parse_error: {e}")
