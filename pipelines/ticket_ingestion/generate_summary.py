"""LLM-генерация выжимки тикета.

Промпт — ``core/prompts/ticket_summary.txt``. Few-shot из ``summary_examples.json``
подаются в виде дополнительного user-сообщения перед основным запросом — так
шаблон ``ticket_summary.txt`` не нужно усложнять.

Парсинг JSON делается через ``Pydantic.model_validate_json`` поверх
``extract_json_object`` (тот срезает обёртки code-fence-ов). При первом
провале — один retry с жёсткой инструкцией «только JSON».
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from config.logging import get_logger
from config.settings import Settings
from core.models import Ticket, TicketSummary
from core.prompts.loader import load_few_shot, load_prompt

from ._json import extract_json_object
from .classify_resolution import ResolutionVerdict

logger = get_logger("pipelines.ticket_ingestion.generate_summary")


class _SummaryLLMResponse(BaseModel):
    summary_one_line: str
    symptom: str
    root_cause: str | None = None
    solution_steps: list[str] = Field(default_factory=list)
    affected_module: str | None = None
    user_role: str | None = None
    is_known_issue: bool = False


def _format_ticket(t: Ticket) -> str:
    parts = [
        f"Тикет {t.external_id}",
        f"Модуль: {t.module or '(не указан)'}",
        f"Категория: {t.category or '(не указана)'}",
        f"Тема: {t.subject}",
        f"Описание: {t.description}",
    ]
    if t.conversation:
        parts.append("Переписка:")
        for c in t.conversation:
            role = c.author_role or "?"
            parts.append(f"  [{role}] {c.content}")
    return "\n".join(parts)


def _format_few_shot(examples: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for ex in examples:
        blocks.append(
            "Пример входа:\n"
            f"{ex['input']}\n\n"
            "Ожидаемый JSON:\n"
            f"{json.dumps(ex['output'], ensure_ascii=False, indent=2)}"
        )
    return "\n\n---\n\n".join(blocks)


class SummaryGenerationError(RuntimeError):
    """Не удалось получить валидную JSON-выжимку даже после retry."""


async def generate_summary(
    ticket: Ticket,
    resolution: ResolutionVerdict,
    llm: LLMClient,
    settings: Settings,
) -> TicketSummary:
    template = load_prompt("ticket_summary")
    system = load_prompt("system_ingest")
    few_shot_text = _format_few_shot(load_few_shot("summary_examples"))

    user_prompt = template.format(ticket_text=_format_ticket(ticket))

    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=f"{few_shot_text}\n\n{user_prompt}"),
    ]

    try:
        response = await llm.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=700,
            json_mode=True,
        )
    except LLMError as e:
        raise SummaryGenerationError(f"LLM error: {e}") from e

    parsed = _try_parse(response.text)
    if parsed is None:
        # один retry с жёсткой инструкцией.
        retry_messages = [
            ChatMessage(
                role="system",
                content="Ты возвращаешь ТОЛЬКО валидный JSON-объект, без обрамления и комментариев.",
            ),
            ChatMessage(role="user", content=f"{few_shot_text}\n\n{user_prompt}"),
        ]
        try:
            response = await llm.chat_completion(
                messages=retry_messages, temperature=0.0, max_tokens=700, json_mode=True
            )
        except LLMError as e:
            raise SummaryGenerationError(f"LLM error on retry: {e}") from e
        parsed = _try_parse(response.text)
        if parsed is None:
            raise SummaryGenerationError(
                f"Не удалось распарсить JSON-выжимку для тикета {ticket.external_id}"
            )

    return TicketSummary(
        ticket_id=ticket.id,
        summary_one_line=parsed.summary_one_line,
        symptom=parsed.symptom,
        root_cause=parsed.root_cause,
        solution_steps=list(parsed.solution_steps),
        affected_module=parsed.affected_module or ticket.module,
        user_role=parsed.user_role,
        is_known_issue=parsed.is_known_issue,
        resolution_status=resolution.resolution_status,
        is_duplicate_of=None,
        generated_at=datetime.utcnow(),
        model_used=response.model,
    )


def _try_parse(text: str) -> _SummaryLLMResponse | None:
    raw = extract_json_object(text)
    try:
        return _SummaryLLMResponse.model_validate_json(raw)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("summary.parse_failed", error=str(e), raw=raw[:300])
        return None
