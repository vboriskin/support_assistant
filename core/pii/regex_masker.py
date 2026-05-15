"""Маскирование на регулярных выражениях.

Регекспы — самый надёжный слой для предсказуемых форматов: email/phone/card/etc.
Где регексп требует контекста (ИНН, паспорт, дата рождения), используем
**capture-group**: вся регулярка ловит «контекст + значение», но в результат
попадает только захваченная группа — иначе маскировка съест слова «ИНН» или
«паспорт» из текста, что портит читаемость.

Регекспы перечислены в порядке специфичности — более узкие выше. Совпадения
объединяются greedy left-to-right: если новый матч пересекается с уже
найденным, он пропускается.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import PIIMatch, PIIType


@dataclass(frozen=True)
class _Rule:
    pii_type: PIIType
    pattern: re.Pattern[str]
    group: int = 0


_RULES: tuple[_Rule, ...] = (
    _Rule(
        PIIType.EMAIL,
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),
    _Rule(
        PIIType.PHONE,
        # +7 / 8 в начале (опционально, со скобками/тире/пробелами),
        # затем код (3 цифры в скобках или без), затем 7 цифр группами.
        re.compile(
            r"(?:\+7|\b8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
        ),
    ),
    _Rule(
        PIIType.CARD,
        re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
    ),
    _Rule(PIIType.ACCOUNT, re.compile(r"\b\d{20}\b")),
    _Rule(PIIType.SNILS, re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")),
    _Rule(
        PIIType.INN,
        re.compile(r"(?i)\bинн[\s:№]*?(\d{10,12})\b"),
        group=1,
    ),
    _Rule(
        PIIType.PASSPORT,
        re.compile(r"(?i)паспорт[^\d]{0,10}(\d{4}\s?\d{6})\b"),
        group=1,
    ),
    _Rule(
        PIIType.APPLICATION_ID,
        re.compile(r"\b(?:APP|ЗПК|КЗ|ЗС)[\-_]?\d{4,}\b"),
    ),
    _Rule(
        PIIType.APPLICATION_ID,
        re.compile(r"(?i)заявк[аеиуы][\s:]+№?\s?(\d{6,})\b"),
        group=1,
    ),
    _Rule(
        PIIType.AMOUNT,
        # Длинная: "1 500 000 руб.", "1,500,000 ₽"
        re.compile(
            r"\b\d{1,3}(?:[\s ,]\d{3})+(?:[.,]\d{1,2})?\s?"
            r"(?:руб(?:лей|\.)?|₽|RUB|р\.)"
        ),
    ),
    _Rule(
        PIIType.AMOUNT,
        # Компактная: "100000 руб", "12345.67 ₽"
        re.compile(r"\b\d{4,}(?:[.,]\d{1,2})?\s?(?:руб(?:лей|\.)?|₽|RUB|р\.)"),
    ),
    _Rule(
        PIIType.BIRTH_DATE,
        re.compile(
            r"(?i)(?:дата\s+рождения|д\.\s?р\.|г\.\s?р\.)[\s:]+"
            r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})"
        ),
        group=1,
    ),
    _Rule(
        PIIType.USER_LOGIN,
        # latin-only логин формата name.surname@domain.zone
        re.compile(r"\b[a-z]{1,20}\.[a-z]{1,20}@[a-z][a-z0-9.\-]{2,30}\b"),
    ),
)


class RegexMasker:
    """Слой regex-маскирования."""

    rules = _RULES

    def find_all(self, text: str) -> list[PIIMatch]:
        if not text:
            return []
        matches: list[PIIMatch] = []
        for rule in self.rules:
            for m in rule.pattern.finditer(text):
                start = m.start(rule.group)
                end = m.end(rule.group)
                if start == -1:
                    continue
                if any(not (end <= ex.start or start >= ex.end) for ex in matches):
                    continue
                matches.append(
                    PIIMatch(
                        pii_type=rule.pii_type,
                        original=m.group(rule.group),
                        start=start,
                        end=end,
                    )
                )
        matches.sort(key=lambda x: x.start)
        return matches
