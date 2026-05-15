"""Stub-адаптер для прямого коннектора к Service Manager API.

Реальный SM API в банке доступен только после согласований/credentials,
поэтому здесь — структурный stub: реализация ``TicketSource``-Protocol с
HTTP-запросами через ``httpx``, контракт ответа описан в docstring'е.

Активация: ``SERVICE_MANAGER_BASE_URL`` + ``SERVICE_MANAGER_TOKEN`` в .env,
вызов через ``create_ticket_source("sm_api")``. До этого endpoint вернёт
``NotImplementedError`` с понятным сообщением.

Ожидаемый формат ответа SM API (нужно уточнить у владельца SM при
интеграции):

::

    GET /tickets?status=closed&since=2026-04-01&page=1&page_size=200
    →
    {
      "items": [
        {
          "id": "SM-12345",
          "created_at": "2026-04-01T10:30:00Z",
          "status": "resolved",
          "channel": "email",
          "category": "Документы",
          "module": "Документы",
          "subject": "...",
          "description": "...",
          "closed_at": "2026-04-01T14:00:00Z",
          "priority": "normal",
          "author_role": "underwriter",
          "assignee": "support_l1",
          "tags": ["загрузка"],
          "comments": [...]
        },
        ...
      ],
      "next_page": 2,
      "has_more": true
    }

Маппинг полей в ``core.models.Ticket`` — в ``_to_ticket()``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from config.logging import get_logger
from core.models import Ticket, TicketComment
from core.security import assert_allowed_llm_host  # тот же whitelist подход

logger = get_logger("adapters.ticket_source.sm_api")


def _parse_dt(value: str) -> datetime:
    # SM API обычно возвращает ISO с 'Z' — нормализуем
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _to_ticket(item: dict[str, Any]) -> Ticket:
    comments = []
    for c in item.get("comments", []) or []:
        try:
            comments.append(
                TicketComment(
                    author_role=c.get("author_role"),
                    content=c.get("content", ""),
                    created_at=_parse_dt(c["created_at"]),
                    is_internal=bool(c.get("is_internal", False)),
                )
            )
        except (KeyError, ValueError):
            continue
    return Ticket(
        id="",
        external_id=str(item["id"]).strip(),
        channel=item.get("channel") or "other",
        category=item.get("category") or None,
        module=item.get("module") or None,
        subject=item["subject"],
        description=item.get("description") or "",
        conversation=comments,
        author_role=item.get("author_role") or None,
        assignee=item.get("assignee") or None,
        status=item["status"],
        priority=item.get("priority") or None,
        tags=list(item.get("tags") or []),
        created_at=_parse_dt(item["created_at"]),
        closed_at=_parse_dt(item["closed_at"]) if item.get("closed_at") else None,
        raw_fields=dict(item),
    )


class ServiceManagerAPISource:
    """Реализация ``TicketSource``-Protocol.

    ``source_uri`` интерпретируется как query-string фильтров для SM API:
    ``status=closed&since=2026-04-01``. Пагинация — внутри (всё, пока
    ``has_more=True``).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_sec: float = 30.0,
        page_size: int = 200,
    ) -> None:
        self.base_url = (base_url or os.getenv("SERVICE_MANAGER_BASE_URL") or "").rstrip("/")
        self.token = token or os.getenv("SERVICE_MANAGER_TOKEN") or ""
        if not self.base_url:
            raise NotImplementedError(
                "Service Manager API не сконфигурирован. "
                "Задайте SERVICE_MANAGER_BASE_URL и SERVICE_MANAGER_TOKEN, "
                "либо используйте CSV-source."
            )
        # Та же защита whitelist, что и для LLM-хостов: подменить URL нельзя
        # без явного добавления хоста в SECURITY_ALLOWED_LLM_HOSTS.
        # Реальный SM-хост скорее всего внутрикорпоративный — добавляется через
        # `SECURITY_ALLOWED_LLM_HOSTS` или (если хочется отдельной переменной)
        # `SERVICE_MANAGER_ALLOWED_HOSTS`.
        assert_allowed_llm_host(
            self.base_url,
            extra_hosts=(os.getenv("SECURITY_ALLOWED_LLM_HOSTS", "")),
        )
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec, connect=10.0), trust_env=False
        )
        self.page_size = page_size

    async def iter_tickets(self, source_uri: str) -> AsyncIterator[Ticket]:
        page = 1
        params_base = dict(_parse_qs(source_uri or ""))
        while True:
            params = {
                **params_base,
                "page": page,
                "page_size": self.page_size,
            }
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            try:
                resp = await self._http.get(
                    f"{self.base_url}/tickets", params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                logger.warning("sm_api.fetch_failed", page=page, error=str(e))
                return
            except ValueError as e:
                logger.warning("sm_api.parse_failed", page=page, error=str(e))
                return

            for item in data.get("items", []):
                try:
                    yield _to_ticket(item)
                except (KeyError, ValueError) as e:
                    logger.warning("sm_api.bad_item", id=item.get("id"), error=str(e))
                    continue

            if not data.get("has_more"):
                return
            page = data.get("next_page", page + 1)

    async def aclose(self) -> None:
        await self._http.aclose()


def _parse_qs(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (s or "").split("&"):
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out
