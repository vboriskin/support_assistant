"""Настройка structlog.

Два режима:
- ``console`` — человекочитаемый цветной вывод для разработки.
- ``json`` — структурный JSON для продакшна.

Вызывайте :func:`configure_logging` один раз при старте приложения.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog
from structlog.types import EventDict, Processor


def _drop_color_message(_: object, __: str, event_dict: EventDict) -> EventDict:
    """uvicorn кладёт `color_message` — в JSON оно не нужно."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(
    level: str = "INFO",
    fmt: Literal["console", "json"] = "console",
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message,
    ]

    if fmt == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
        shared_processors.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    for noisy in ("urllib3", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
