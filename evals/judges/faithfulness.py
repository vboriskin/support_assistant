"""LLM-judge faithfulness: насколько ответ системы поддержан источниками.

Если источников нет — `1.0` если в ответе явный отказ «не знаю» (это
ожидаемое поведение), иначе `0.0`. Промпт — ``judge_faithfulness.txt``.
"""

from __future__ import annotations

import json

from adapters.llm.base import ChatMessage, LLMClient
from adapters.llm.exceptions import LLMError
from config.logging import get_logger
from core.models import Answer, Source
from core.prompts.loader import load_prompt
from pipelines.ticket_ingestion._json import extract_json_object

logger = get_logger("evals.judges.faithfulness")

_NO_ANSWER_MARKERS = ("не знаю", "нет информации")


class FaithfulnessJudge:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def evaluate(self, *, answer: Answer, sources: list[Source]) -> tuple[float, str]:
        text_lower = (answer.text or "").lower()
        if not sources:
            if any(m in text_lower for m in _NO_ANSWER_MARKERS):
                return 1.0, "no_sources_and_honest_decline"
            return 0.0, "no_sources_but_answer_given"

        template = load_prompt("judge_faithfulness")
        sources_block = "\n\n".join(
            f"[{i + 1}] {s.title}\n{s.content[:600]}" for i, s in enumerate(sources)
        )
        prompt = template.format(sources=sources_block, answer=answer.text)
        try:
            response = await self.llm.chat_completion(
                messages=[
                    ChatMessage(
                        role="system",
                        content="Ты — строгий судья faithfulness. Отвечай только JSON.",
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
            score = float(data.get("faithfulness_score", 0.0))
            reasoning = str(data.get("reasoning", ""))
            return max(0.0, min(1.0, score)), reasoning
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("faithfulness.parse_error", error=str(e), raw=raw[:200])
            return 0.0, f"parse_error: {e}"
