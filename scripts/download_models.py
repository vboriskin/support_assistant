"""Скачивает модель эмбеддингов в локальный кэш.

Запуск:

    python -m scripts.download_models

Скрипт нужен, чтобы вынести сетевую загрузку (несколько ГБ) за пределы первого
запроса в проде. На стенде без интернета — собрать кэш на dev-машине, перенести
``EMBEDDINGS_CACHE_DIR`` в контур.
"""

from __future__ import annotations

import sys

from config.logging import configure_logging, get_logger
from config.settings import get_settings


def main() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("download_models")

    if settings.embeddings.provider == "mock":
        log.info("skip_mock_provider", provider="mock")
        return 0

    s = settings.embeddings
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    log.info("downloading", model=s.model_name, cache_dir=str(s.cache_dir), device=s.device)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        log.error(
            "sentence_transformers_not_installed",
            hint="установите зависимости: pip install -e '.[dev]' или sentence-transformers",
        )
        return 2

    model = SentenceTransformer(s.model_name, cache_folder=str(s.cache_dir), device=s.device)
    dim = model.get_sentence_embedding_dimension()
    log.info("downloaded", dimension=dim, expected=s.dimension)
    if dim and dim != s.dimension:
        log.warning(
            "dimension_mismatch",
            actual=dim,
            expected=s.dimension,
            hint="скорректируйте EMBEDDINGS_DIMENSION в .env",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
