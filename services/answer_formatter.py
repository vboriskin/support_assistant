"""Парсер ответа LLM: извлекает цитаты ``[N]``.

Поведение:

- Сканируем текст по паттерну ``\\[(\\d+)\\]``.
- Цитата принимается, если индекс в диапазоне ``1..len(used_sources)``;
  «битые» индексы (``[99]``, когда у нас 8 источников) — отбрасываем.
- ``used_sources`` отдаём всё, что было найдено retriever'ом: UI хочет
  показывать панель источников, даже если LLM не процитировал их явно.
"""

from __future__ import annotations

import re

from core.models import Answer, Citation, Source


class AnswerFormatter:
    _CITATION_RE = re.compile(r"\[(\d+)\]")

    def parse(
        self,
        *,
        text: str,
        used_sources: list[Source],
        model: str,
        latency_ms: int,
        token_usage: dict[str, int | None] | None = None,
        conversation_id: str | None = None,
    ) -> Answer:
        cited_indices: set[int] = set()
        for m in self._CITATION_RE.finditer(text or ""):
            try:
                idx = int(m.group(1))
            except ValueError:
                continue
            if 1 <= idx <= len(used_sources):
                cited_indices.add(idx)
        citations = [
            Citation(source_index=idx, source=used_sources[idx - 1])
            for idx in sorted(cited_indices)
        ]
        return Answer(
            text=(text or "").strip(),
            citations=citations,
            used_sources=used_sources,
            model_used=model,
            latency_ms=latency_ms,
            token_usage=token_usage,
            conversation_id=conversation_id,
        )
