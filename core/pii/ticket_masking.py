"""Маскирование PII в ``Ticket`` целиком.

Маскируем поля, где встречается клиентский текст:

- ``subject``;
- ``description``;
- ``conversation[i].content``.

Поля ``category``/``module``/``priority``/``status``/``tags`` по контракту
наполняются СМ-метаданными, не PII — их не трогаем. Аналогично ``author_role``/
``assignee``: по спецификации это **роли** ("оператор", "андеррайтер"), не ФИО.
Если в выгрузке оказались имена — это сигнал, что в CSV нарушен контракт; на
этот случай можно расширить маскирование, но дефолтом не делаем, чтобы не
терять полезные роли.
"""

from __future__ import annotations

from core.models import Ticket

from .pipeline import PIIMaskingPipeline


def _merge_audit(dst: dict[str, int], src: dict[str, int]) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0) + v


def mask_ticket(ticket: Ticket, pipeline: PIIMaskingPipeline) -> tuple[Ticket, dict[str, int]]:
    """Возвращает новый ``Ticket`` с маскированными полями и сводный аудит."""
    masked = ticket.model_copy(deep=True)
    audit: dict[str, int] = {}

    s_res = pipeline.mask(masked.subject)
    masked.subject = s_res.masked_text
    _merge_audit(audit, s_res.audit)

    d_res = pipeline.mask(masked.description)
    masked.description = d_res.masked_text
    _merge_audit(audit, d_res.audit)

    for comment in masked.conversation:
        c_res = pipeline.mask(comment.content)
        comment.content = c_res.masked_text
        _merge_audit(audit, c_res.audit)

    return masked, audit
