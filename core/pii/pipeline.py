"""Композиция regex + NER + strict sanity-check."""

from __future__ import annotations

import re
from dataclasses import dataclass

from config.settings import Settings

from .ner_masker import NERMasker
from .regex_masker import RegexMasker
from .types import PIIMatch, PIIRemainsError, PIIType


@dataclass(frozen=True)
class MaskingResult:
    masked_text: str
    audit: dict[str, int]
    matches: list[PIIMatch]


# Простые «остаточные» паттерны для strict-mode. Не пытаемся повторить весь
# RegexMasker — задача проверки в другом: убедиться, что не пропустили самое
# очевидное (email, нормальный телефон, 16 цифр подряд).
_SANITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d{16}\b"),
)


class PIIMaskingPipeline:
    """Полный пайплайн маскирования PII в строке."""

    def __init__(self, settings: Settings) -> None:
        self.regex = RegexMasker()
        self.ner = NERMasker() if settings.pii.ner_enabled else None
        self.strict = settings.pii.strict_mode

    def mask(self, text: str) -> MaskingResult:
        if not text:
            return MaskingResult(masked_text=text, audit={}, matches=[])

        matches: list[PIIMatch] = list(self.regex.find_all(text))
        if self.ner is not None:
            for m in self.ner.find_all(text):
                # NER уступает regex'у: если есть пересечение, пропускаем.
                if any(not (m.end <= ex.start or m.start >= ex.end) for ex in matches):
                    continue
                matches.append(m)
        matches.sort(key=lambda m: m.start)

        # Применяем замены справа налево, чтобы не сбивать индексы исходного текста.
        masked = text
        audit: dict[str, int] = {}
        for m in reversed(matches):
            token = f"<{m.pii_type.value}>"
            masked = masked[: m.start] + token + masked[m.end :]
            audit[m.pii_type.value] = audit.get(m.pii_type.value, 0) + 1

        if self.strict:
            self._sanity_check(masked)

        return MaskingResult(masked_text=masked, audit=audit, matches=matches)

    @staticmethod
    def _sanity_check(masked: str) -> None:
        for pat in _SANITY_PATTERNS:
            m = pat.search(masked)
            if m:
                raise PIIRemainsError(
                    f"strict-mode: остаточный PII-паттерн в тексте: {m.group(0)!r}"
                )

    # Удобный публичный хелпер для строгого определения типа без публичного импорта
    masking_types = PIIType
