"""LLM-judge helpfulness: насколько ответ полезен оператору 1-й линии.

Если кейс — ``no_answer_in_kb`` и ответ системы явно «не знаю», возвращаем 1.0
без LLM-вызова (это эталонное поведение, см. ``judge_helpfulness.txt`` §"Если
запрос — это no-answer-in-kb сценарий и ответ системы — честный «не знаю»,
это 1.0").
"""

from __future__ import annotations

import json

from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from config.logging import get_logger
from core.models import Answer
from core.prompts.loader import load_prompt
from pipelines.ticket_ingestion._json import extract_json_object

logger = get_logger("evals.judges.helpfulness")

_NO_ANSWER_MARKERS = ("не знаю", "нет информации")


class HelpfulnessJudge:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def evaluate(
        self,
        *,
        query: str,
        answer: Answer,
        expected_summary: str,
        no_answer_expected: bool = False,
    ) -> tuple[float, str]:
        text_lower = (answer.text or "").lower()
        if no_answer_expected and any(m in text_lower for m in _NO_ANSWER_MARKERS):
            return 1.0, "no_answer_expected_and_declined"

        template = load_prompt("judge_helpfulness")
        prompt = template.format(
            query=query,
            answer=answer.text,
            expected_summary=expected_summary or "(эталон не задан)",
        )
        try:
            response = await self.llm.chat_completion(
                messages=[
                    ChatMessage(
                        role="system",
                        content="Ты — судья полезности ответа. Отвечай только JSON.",
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                temperature=0.0,
                max_tokens=400,
                json_mode=True,
            )
        except LLMError as e:
            return 0.0, f"llm_error: {e}"

        raw = extract_json_object(response.text)
        try:
            data = json.loads(raw)
            score = float(data.get("helpfulness_score", 0.0))
            reasoning = str(data.get("reasoning", ""))
            return max(0.0, min(1.0, score)), reasoning
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("helpfulness.parse_error", error=str(e), raw=raw[:200])
            return 0.0, f"parse_error: {e}"
