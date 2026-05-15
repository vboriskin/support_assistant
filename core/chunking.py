"""Семантический чанкинг текста для KB-индексации.

Стратегия:

1. Разбиение по заголовкам markdown (``#``, ``##``, ``###``) — каждая секция
   становится логическим блоком.
2. Если секция длиннее ``max_chars`` — режем по абзацам (``\\n\\n``),
   затем по предложениям. С overlap'ом ``overlap_chars``, чтобы граничные
   термины не теряли контекст.
3. Минимальный чанк — ``min_chars`` (короче не индексируем; обычно это
   мусор вроде «См. ниже»).

Чанки несут метаданные:

- ``section_title`` — заголовок секции, в которой находится чанк.
- ``chunk_order`` — порядковый номер внутри статьи.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.!?…])\s+(?=[А-ЯA-Z])")


@dataclass
class Chunk:
    text: str
    section_title: str | None
    chunk_order: int


def _split_by_headings(text: str) -> list[tuple[str | None, str]]:
    """Возвращает [(section_title, section_body), ...]."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(None, text.strip())]
    sections: list[tuple[str | None, str]] = []
    # Префикс до первого заголовка — без title
    first = matches[0]
    if first.start() > 0:
        head = text[: first.start()].strip()
        if head:
            sections.append((None, head))
    for i, m in enumerate(matches):
        title = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            sections.append((title, body))
    return sections


def _split_long(body: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    """Режет длинный текст по абзацам с overlap'ом."""
    if len(body) <= max_chars:
        return [body]
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    out: list[str] = []
    cur = ""
    for p in paragraphs:
        if len(cur) + len(p) + 2 <= max_chars:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                out.append(cur)
            # overlap из хвоста предыдущего
            tail = cur[-overlap_chars:] if overlap_chars and cur else ""
            cur = (tail + "\n\n" + p).strip() if tail else p
            # если параграф сам по себе слишком длинный — режем по предложениям
            while len(cur) > max_chars:
                sentences = _SENTENCE_SPLIT_RE.split(cur)
                if len(sentences) == 1:
                    # ни одного break-point'а: жёстко режем по символам
                    out.append(cur[:max_chars])
                    cur = cur[max_chars - overlap_chars :]
                else:
                    half = max_chars
                    acc = ""
                    rest_idx = 0
                    for si, s in enumerate(sentences):
                        if len(acc) + len(s) + 1 > half:
                            rest_idx = si
                            break
                        acc = f"{acc} {s}" if acc else s
                    out.append(acc.strip())
                    cur = " ".join(sentences[rest_idx:]).strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(
    text: str,
    *,
    max_chars: int = 1200,
    min_chars: int = 80,
    overlap_chars: int = 100,
) -> list[Chunk]:
    """Возвращает упорядоченный список чанков."""
    if not text or not text.strip():
        return []
    out: list[Chunk] = []
    order = 0
    for title, body in _split_by_headings(text):
        for piece in _split_long(body, max_chars=max_chars, overlap_chars=overlap_chars):
            piece = piece.strip()
            if len(piece) < min_chars:
                continue
            out.append(Chunk(text=piece, section_title=title, chunk_order=order))
            order += 1
    return out
