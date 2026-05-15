"""Базовый интерфейс источника тикетов."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from core.models import Ticket


@runtime_checkable
class TicketSource(Protocol):
    """Источник, который умеет ленива выдавать ``Ticket`` по URI.

    URI может быть путём к CSV-файлу, идентификатором SM-выгрузки и т.п. —
    интерпретация остаётся за конкретной реализацией.
    """

    def iter_tickets(self, source_uri: str) -> AsyncIterator[Ticket]: ...
