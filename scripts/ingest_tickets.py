"""CLI для запуска ингеста CSV → индекс.

    python -m scripts.ingest_tickets /path/to/tickets.csv

Использует ту же конфигурацию из ``.env``, что и сервер: LLM/embeddings/db
выбираются по переменным окружения. Для прогона без сети — выставляйте
``LLM_PROVIDER=mock`` и ``EMBEDDINGS_PROVIDER=mock``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from adapters.embeddings.factory import create_embeddings_client
from adapters.llm.factory import create_llm_client
from adapters.text_search.factory import create_text_search
from adapters.ticket_source.factory import create_ticket_source
from adapters.vector_store.factory import create_vector_store
from config.logging import configure_logging, get_logger
from config.settings import get_settings
from core.pii.pipeline import PIIMaskingPipeline
from db.engine import dispose_engine, get_engine, get_session_factory
from pipelines.ticket_ingestion.pipeline import TicketIngestionPipeline


async def _run(csv_path: Path) -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("scripts.ingest_tickets")

    engine = get_engine(settings)
    session_factory: async_sessionmaker = get_session_factory(settings)

    llm = create_llm_client(settings)
    embeddings = create_embeddings_client(settings)
    vector_store = create_vector_store(settings, engine)
    text_search = create_text_search(settings, engine)
    pii = PIIMaskingPipeline(settings)
    source = create_ticket_source("csv")

    pipeline = TicketIngestionPipeline(
        settings=settings,
        source=source,
        session_factory=session_factory,
        llm=llm,
        embeddings=embeddings,
        vector_store=vector_store,
        text_search=text_search,
        pii_pipeline=pii,
    )

    log.info("ingest.start", csv=str(csv_path))
    try:
        stats = await pipeline.run(str(csv_path))
    finally:
        await llm.aclose()
        await embeddings.aclose()
        await dispose_engine()

    log.info("ingest.done", **stats)
    print(stats)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest tickets from CSV")
    parser.add_argument("csv_path", type=Path, help="путь к CSV-файлу")
    args = parser.parse_args(argv)
    if not args.csv_path.exists():
        print(f"file not found: {args.csv_path}", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.csv_path))


if __name__ == "__main__":
    sys.exit(main())
