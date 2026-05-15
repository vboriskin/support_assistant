"""Парсинг одной CSV-строки в ``Ticket``.

Сам читатель CSV-файла — в ``adapters/ticket_source/csv_source.py``.
Здесь — детерминированный pure-парсер словаря строки в доменную модель.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.models import Ticket, TicketComment

_REQUIRED = ("external_id", "created_at", "status", "subject", "description")

# ``Ticket`` имеет строгие Literal-каналы и статусы. Если в CSV приходит что-то
# незнакомое — приводим к безопасным значениям, чтобы не падать на каждой строке.
_VALID_CHANNELS = {"email", "messenger", "chatbot", "sm", "phone", "other"}
_VALID_STATUSES = {"open", "in_progress", "resolved", "closed", "cancelled"}
_VALID_PRIORITIES = {"low", "normal", "high", "critical"}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_csv_row(row: dict[str, Any]) -> Ticket:
    for field in _REQUIRED:
        if not row.get(field):
            raise ValueError(f"missing required field: {field}")

    try:
        created_at = _parse_dt(row["created_at"])
    except ValueError as e:
        raise ValueError(f"invalid created_at: {row['created_at']!r}") from e

    closed_at: datetime | None = None
    if row.get("closed_at"):
        try:
            closed_at = _parse_dt(row["closed_at"])
        except ValueError:
            closed_at = None

    conversation: list[TicketComment] = []
    if row.get("conversation"):
        try:
            raw_conv = json.loads(row["conversation"])
            for item in raw_conv:
                conversation.append(
                    TicketComment(
                        author_role=item.get("author_role"),
                        content=item.get("content", ""),
                        created_at=_parse_dt(item["created_at"]),
                        is_internal=bool(item.get("is_internal", False)),
                    )
                )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            # Битый conversation — не блокер для остальных полей.
            conversation = []

    tags: list[str] = []
    if row.get("tags"):
        tags = [t.strip() for t in str(row["tags"]).split(",") if t.strip()]

    channel = row.get("channel") or "other"
    if channel not in _VALID_CHANNELS:
        channel = "other"

    status = row["status"]
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status: {status!r}")

    priority = row.get("priority") or None
    if priority is not None and priority not in _VALID_PRIORITIES:
        priority = None

    return Ticket(
        id="",  # будет сгенерирован репозиторием при сохранении
        external_id=str(row["external_id"]).strip(),
        channel=channel,
        category=row.get("category") or None,
        module=row.get("module") or None,
        subject=row["subject"],
        description=row["description"],
        conversation=conversation,
        author_role=row.get("author_role") or None,
        assignee=row.get("assignee") or None,
        status=status,
        priority=priority,
        tags=tags,
        created_at=created_at,
        closed_at=closed_at,
        raw_fields=dict(row),
    )
