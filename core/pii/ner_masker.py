"""NER-маскирование через Natasha.

Natasha — тяжёлая зависимость (загрузка моделей при первом ``Doc``-вызове, ~1 сек,
память ~700 МБ). Чтобы не платить за инициализацию в окружениях без неё:

- импорты — внутри ``__init__`` (ленивые);
- если пакет недоступен, маскер деградирует в no-op (``find_all`` возвращает ``[]``);
- доступность можно явно отключить через ``PII_NER_ENABLED=false``.

Что ловим:

- ``PER`` → :class:`PIIType.PERSON`;
- ``LOC`` → :class:`PIIType.ADDRESS`, но **только** если в контексте слева
  есть слова-индикаторы адреса ("улица", "дом", "г.", "город", "адрес").
  Без этого Natasha будет помечать "Россия"/"Москва"/название отдела — это
  не PII в нашем понимании.
"""

from __future__ import annotations

from config.logging import get_logger

from .types import PIIMatch, PIIType

logger = get_logger("core.pii.ner")

_ADDRESS_HINTS = ("ул.", "улица", "дом ", "г.", "город", "адрес", "проспект", "пр-кт")


class NERMasker:
    def __init__(self) -> None:
        try:
            from natasha import (
                Doc,
                NewsEmbedding,
                NewsMorphTagger,
                NewsNERTagger,
                Segmenter,
            )

            self._Doc = Doc
            self._segmenter = Segmenter()
            emb = NewsEmbedding()
            self._morph_tagger = NewsMorphTagger(emb)
            self._ner_tagger = NewsNERTagger(emb)
            self._available = True
        except ImportError:
            logger.warning("natasha.not_installed")
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def find_all(self, text: str) -> list[PIIMatch]:
        if not self._available or not text:
            return []
        try:
            doc = self._Doc(text)
            doc.segment(self._segmenter)
            doc.tag_morph(self._morph_tagger)
            doc.tag_ner(self._ner_tagger)
        except Exception as e:  # pragma: no cover — защита от багов Natasha
            logger.warning("ner.failed", error=str(e))
            return []

        matches: list[PIIMatch] = []
        for span in doc.spans:
            if span.type == "PER":
                matches.append(
                    PIIMatch(
                        pii_type=PIIType.PERSON,
                        original=text[span.start : span.stop],
                        start=span.start,
                        end=span.stop,
                        confidence=0.85,
                    )
                )
            elif span.type == "LOC":
                ctx_start = max(0, span.start - 20)
                ctx = text[ctx_start : span.start].lower()
                if any(h in ctx for h in _ADDRESS_HINTS):
                    matches.append(
                        PIIMatch(
                            pii_type=PIIType.ADDRESS,
                            original=text[span.start : span.stop],
                            start=span.start,
                            end=span.stop,
                            confidence=0.7,
                        )
                    )
        return matches
