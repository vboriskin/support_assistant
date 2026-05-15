"""Шаг маскирования PII — тонкая обёртка над ``core.pii.ticket_masking``."""

from __future__ import annotations

from core.models import Ticket
from core.pii.pipeline import PIIMaskingPipeline
from core.pii.ticket_masking import mask_ticket as _mask


def mask_ticket(ticket: Ticket, pipeline: PIIMaskingPipeline) -> tuple[Ticket, dict[str, int]]:
    """Возвращает копию тикета с замаскированными полями и сводный аудит."""
    return _mask(ticket, pipeline)
