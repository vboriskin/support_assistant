"""Интеграционные тесты ``LLMLogsRepository``."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.llm_logs import LLMLogsRepository


@pytest.mark.integration
async def test_record_hashes_prompt_and_trims_previews(session: AsyncSession) -> None:
    repo = LLMLogsRepository(session)
    prompt = "ABC" * 1000
    response = "X" * 800
    log = await repo.record(
        purpose="answer",
        model="mock-llm",
        prompt=prompt,
        response=response,
        latency_ms=42,
        prompt_tokens=100,
        completion_tokens=50,
        user_id="alice",
    )
    await session.commit()

    assert log.prompt_hash == hashlib.sha256(prompt.encode()).hexdigest()
    assert log.prompt_preview is not None and len(log.prompt_preview) == 500
    assert log.response_preview is not None and len(log.response_preview) == 500
    assert log.latency_ms == 42
    assert log.user_id == "alice"


@pytest.mark.integration
async def test_list_recent_filters_by_purpose(session: AsyncSession) -> None:
    repo = LLMLogsRepository(session)
    await repo.record(purpose="answer", model="m", prompt="a", response="r", latency_ms=1)
    await repo.record(purpose="summary", model="m", prompt="b", response="r", latency_ms=2)
    await repo.record(purpose="answer", model="m", prompt="c", response="r", latency_ms=3)
    await session.commit()

    answers = await repo.list_recent(purpose="answer")
    assert len(answers) == 2
    assert {log.purpose for log in answers} == {"answer"}


@pytest.mark.integration
async def test_record_with_error(session: AsyncSession) -> None:
    repo = LLMLogsRepository(session)
    log = await repo.record(
        purpose="answer",
        model="m",
        prompt="p",
        response=None,
        latency_ms=10,
        error="timeout",
    )
    await session.commit()
    assert log.error == "timeout"
    assert log.response_preview is None
