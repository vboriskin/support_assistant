"""Загрузчик HTML-файлов KB (типичный экспорт из Confluence).

Без bs4: простая чистка regex'ами через ``core.text_cleaning._strip_html``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from core.text_cleaning import clean_text
from pipelines.kb_ingestion.markdown_loader import RawArticle

_TITLE_RE = re.compile(r"<title[^>]*>(.+?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.+?)</h1>", re.IGNORECASE | re.DOTALL)


def _title_from_html(html: str, fallback: str) -> str:
    m = _H1_RE.search(html) or _TITLE_RE.search(html)
    if not m:
        return fallback
    return clean_text(m.group(1))[:200] or fallback


def iter_html_files(root: str | Path) -> Iterator[RawArticle]:
    root_path = Path(root)
    paths = [root_path] if root_path.is_file() else sorted(root_path.rglob("*.html"))
    for p in paths:
        raw = p.read_text(encoding="utf-8", errors="replace")
        body = clean_text(raw)
        if not body:
            continue
        yield RawArticle(
            title=_title_from_html(raw, fallback=p.stem),
            body=body,
            source_path=str(p),
        )
