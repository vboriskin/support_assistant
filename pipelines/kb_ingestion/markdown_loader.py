"""Загрузчик markdown-файлов KB.

Контракт: на вход — путь к директории (или файлу). На выход — список
"raw articles" (title, body, source_path). Title берётся из первого
``# Heading`` или из имени файла.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_FIRST_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class RawArticle:
    title: str
    body: str
    source_path: str


def _title_from(text: str, fallback: str) -> str:
    m = _FIRST_HEADING_RE.search(text)
    return m.group(1).strip() if m else fallback


def iter_markdown_files(root: str | Path) -> Iterator[RawArticle]:
    """Рекурсивно проходит по ``*.md`` файлам."""
    root_path = Path(root)
    if root_path.is_file():
        paths = [root_path]
    else:
        paths = sorted(root_path.rglob("*.md"))
    for p in paths:
        body = p.read_text(encoding="utf-8").strip()
        if not body:
            continue
        yield RawArticle(
            title=_title_from(body, fallback=p.stem),
            body=body,
            source_path=str(p),
        )
