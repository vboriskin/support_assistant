"""Нормализация тикета — обёртка над ``core.text_cleaning.clean_text``."""

from __future__ import annotations

from core.models import Ticket
from core.text_cleaning import clean_text


def normalize_ticket(ticket: Ticket) -> Ticket:
    """Возвращает новый ``Ticket`` с очищенными ``subject``/``description``/комментариями.

    Пустые после чистки комментарии выкидываются — они только шумят на индексации.
    """
    out = ticket.model_copy(deep=True)
    out.subject = clean_text(out.subject)
    out.description = clean_text(out.description)
    kept = []
    for c in out.conversation:
        cleaned = clean_text(c.content)
        if cleaned:
            c.content = cleaned
            kept.append(c)
    out.conversation = kept
    return out
