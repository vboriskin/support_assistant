"""Чистка пользовательского текста.

Уровень — простые регулярки. Сложный HTML/MIME-парсинг намеренно не делаем:
тикеты в нашей выгрузке обычно это plain text c минимумом разметки. Если
позднее окажется, что в сырых данных встречается сложный HTML — добавим
``beautifulsoup4`` в зависимости и подменим ``_strip_html``.

Шаги (в порядке применения):

1. ``_strip_html`` — удаляем теги через regex (никаких атрибутов и скриптов).
2. ``_strip_quotes_and_signatures`` — режем цитаты ``>``, блоки ``-----
   Original Message -----``, шапку «От: Кому: Тема:» и хвост после подписи.
3. ``_normalize_whitespace`` — `\r\n` → `\n`, 3+ переносов → 2, схлопывание
   пробелов внутри строки.
"""

from __future__ import annotations

import re

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&(nbsp|amp|lt|gt|quot|#\d+);")
_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
}

_QUOTE_LINE_RE = re.compile(r"^>+.*$", re.MULTILINE)
_ORIGINAL_MSG_RE = re.compile(
    r"-{3,}\s*Original Message\s*-{3,}.*",
    re.DOTALL | re.IGNORECASE,
)
_EMAIL_HEADER_BLOCK_RE = re.compile(
    r"\n(?:От|From|Кому|To|Тема|Subject)[: ].*?(?=\n\n|\Z)",
    re.DOTALL,
)

_SIGNATURE_HINTS = (
    "С уважением",
    "С наилучшими пожеланиями",
    "Best regards",
    "Regards,",
    "Kind regards",
)

_NEWLINE_NORMALIZE_RE = re.compile(r"\r\n?")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")


def _strip_html(s: str) -> str:
    if "<" not in s and "&" not in s:
        return s
    out = _HTML_TAG_RE.sub(" ", s)

    def _entity(m: re.Match[str]) -> str:
        ent = m.group(0)
        if ent in _HTML_ENTITIES:
            return _HTML_ENTITIES[ent]
        # &#NNN; — числовая сущность
        if ent.startswith("&#"):
            try:
                return chr(int(ent[2:-1]))
            except ValueError:
                return " "
        return " "

    return _HTML_ENTITY_RE.sub(_entity, out)


def _strip_quotes_and_signatures(s: str) -> str:
    s = _QUOTE_LINE_RE.sub("", s)
    s = _ORIGINAL_MSG_RE.sub("", s)
    s = _EMAIL_HEADER_BLOCK_RE.sub("", s)

    # Подпись: ищем последний из подсказочных маркеров в последних 12 строках
    # — режем всё после него.
    lines = s.splitlines()
    cut_at: int | None = None
    window_start = max(0, len(lines) - 12)
    for i in range(window_start, len(lines)):
        line = lines[i]
        if any(hint in line for hint in _SIGNATURE_HINTS):
            cut_at = i
            break
        # «-- » в одиночной строке — классический разделитель подписи в email
        if line.strip() == "--":
            cut_at = i
            break
    if cut_at is not None:
        s = "\n".join(lines[:cut_at])
    return s


def _normalize_whitespace(s: str) -> str:
    s = _NEWLINE_NORMALIZE_RE.sub("\n", s)
    s = _MULTI_NEWLINE_RE.sub("\n\n", s)
    s = "\n".join(_MULTI_SPACE_RE.sub(" ", line).rstrip() for line in s.split("\n"))
    return s.strip()


def clean_text(s: str) -> str:
    """Полный пайплайн чистки одной строки."""
    if not s:
        return s
    s = _strip_html(s)
    s = _strip_quotes_and_signatures(s)
    s = _normalize_whitespace(s)
    return s
