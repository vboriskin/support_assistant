"""Репозиторий ``llm_call_logs`` — аудит каждого LLM-вызова.

Что обязательно фиксируем:

- цель вызова (``purpose`` = ``answer`` / ``summary`` / ``categorize`` / ``judge``);
- модель и длительность;
- ``prompt_hash`` (SHA256) — чтобы сравнивать одинаковые промпты в разных
  прогонах evals;
- ``prompt_preview`` / ``response_preview`` — первые 500 символов; полные
  тексты в логах **никогда**, чтобы не утянуть PII.

Полный prompt/response может писаться в structured-лог отдельно, но в БД
держим только preview — этого достаточно для отладки.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import LLMCallLog

_PREVIEW_LEN = 500


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class LLMLogsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        purpose: str,
        model: str,
        prompt: str,
        response: str | None,
        latency_ms: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        user_id: str | None = None,
        error: str | None = None,
        id: str | None = None,
    ) -> LLMCallLog:
        log = LLMCallLog(
            id=id or str(uuid.uuid4()),
            user_id=user_id,
            purpose=purpose,
            model=model,
            prompt_hash=_hash_prompt(prompt),
            prompt_preview=prompt[:_PREVIEW_LEN] if prompt else None,
            response_preview=response[:_PREVIEW_LEN] if response else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            error=error,
            created_at=_now(),
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_recent(
        self,
        *,
        purpose: str | None = None,
        limit: int = 100,
    ) -> list[LLMCallLog]:
        stmt = select(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(limit)
        if purpose is not None:
            stmt = stmt.where(LLMCallLog.purpose == purpose)
        return list((await self.session.execute(stmt)).scalars().all())
