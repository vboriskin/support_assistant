"""Фабрика источников тикетов.

На MVP — только ``csv``. В перспективе тут появится коннектор к Service Manager
API. Выбор по схеме URI (``csv:`` префикс или просто путь к ``.csv``).
"""

from __future__ import annotations

from .base import TicketSource
from .csv_source import CSVTicketSource


def create_ticket_source(kind: str = "csv") -> TicketSource:
    if kind == "csv":
        return CSVTicketSource()
    if kind == "sm_api":
        from .sm_api import ServiceManagerAPISource

        return ServiceManagerAPISource()
    raise ValueError(f"Unknown ticket source kind: {kind}")
