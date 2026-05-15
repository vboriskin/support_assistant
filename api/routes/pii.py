"""POST /api/pii/test — PII regex playground.

Принимает текст и опциональный список доп.regex-паттернов. Возвращает
маскированную версию и список найденных совпадений (label, match, start, end).
Используется UI-вкладкой «PII playground» для тестирования regex'ов до того,
как они уедут в продовый конфиг.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.pii.regex_masker import RegexMasker
from core.pii.types import PIIMatch

router = APIRouter(prefix="/pii", tags=["pii"])


class PIIExtraPattern(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    pattern: str = Field(..., min_length=1, max_length=500)


class PIITestRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)
    extra_patterns: list[PIIExtraPattern] = Field(default_factory=list)


@router.post("/test")
async def test_pii(body: PIITestRequest) -> dict[str, Any]:
    base = RegexMasker()
    matches: list[PIIMatch] = list(base.find_all(body.text))

    # Доп. паттерны от пользователя — компилируем здесь, аккуратно ловим
    # «битые» regex'ы и сообщаем понятно.
    extra_compiled: list[tuple[str, re.Pattern[str]]] = []
    for ep in body.extra_patterns:
        try:
            extra_compiled.append((ep.label, re.compile(ep.pattern)))
        except re.error as e:
            raise HTTPException(422, f"bad regex for '{ep.label}': {e}") from e

    for label, pat in extra_compiled:
        for m in pat.finditer(body.text):
            # имитируем PIIMatch с произвольным "label"; чтобы не плодить enum,
            # просто пишем в отдельной структуре.
            matches.append(
                _AdHocMatch(  # type: ignore[arg-type]
                    label=label, start=m.start(), end=m.end(), value=m.group(0)
                )
            )

    # Применяем замены справа налево
    matches_sorted = sorted(matches, key=lambda m: m.start)
    masked = body.text
    for m in reversed(matches_sorted):
        if isinstance(m, _AdHocMatch):
            token = f"<{m.label}>"
        else:
            token = f"<{m.pii_type.value}>"
        masked = masked[: m.start] + token + masked[m.end :]

    return {
        "masked_text": masked,
        "matches": [
            {
                "label": m.label if isinstance(m, _AdHocMatch) else m.pii_type.value,
                "value": (m.value if isinstance(m, _AdHocMatch) else body.text[m.start:m.end]),
                "start": m.start,
                "end": m.end,
                "source": "extra" if isinstance(m, _AdHocMatch) else "builtin",
            }
            for m in matches_sorted
        ],
    }


class _AdHocMatch:
    """Лёгкая структура для совпадений из произвольных regex'ов."""

    __slots__ = ("label", "start", "end", "value")

    def __init__(self, *, label: str, start: int, end: int, value: str) -> None:
        self.label = label
        self.start = start
        self.end = end
        self.value = value
