"""Источник тикетов из CSV-файла.

Контракт CSV — в ``docs/03-DATA-MODELS.md``. Если строка не валидна — пишем
``warning`` и идём дальше; одна битая строка не должна валить весь job.
"""

from __future__ import annotations

import csv
from collections.abc import AsyncIterator
from pathlib import Path

from config.logging import get_logger
from core.models import Ticket
from pipelines.ticket_ingestion.extract import parse_csv_row

logger = get_logger("adapters.ticket_source.csv")


class CSVTicketSource:
    async def iter_tickets(self, source_uri: str) -> AsyncIterator[Ticket]:
        path = Path(source_uri)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for line_num, row in enumerate(reader, start=2):
                try:
                    yield parse_csv_row(row)
                except ValueError as e:
                    logger.warning(
                        "csv.row.invalid",
                        line=line_num,
                        external_id=row.get("external_id"),
                        error=str(e),
                    )
                    continue
