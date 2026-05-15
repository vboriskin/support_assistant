"""Тесты ``MockLLMClient`` и фабрики ``create_llm_client``."""

from __future__ import annotations

import pytest

from adapters.llm.base import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatMessage,
    LLMClient,
)
from adapters.llm.factory import create_llm_client
from adapters.llm.mock import MockLLMClient
from config.settings import Settings


@pytest.mark.unit
async def test_mock_chat_completion_returns_response_model() -> None:
    client = MockLLMClient()
    resp = await client.chat_completion(
        [ChatMessage(role="user", content="Hello?")],
    )
    assert isinstance(resp, ChatCompletionResponse)
    assert resp.finish_reason == "stop"
    assert resp.text  # непустой
    assert resp.model == "mock-llm"
    await client.aclose()


@pytest.mark.unit
async def test_mock_returns_configured_response_by_substring() -> None:
    client = MockLLMClient(responses={"reset password": "Use the reset link."})
    resp = await client.chat_completion(
        [ChatMessage(role="user", content="How do I reset password for client?")],
    )
    assert resp.text == "Use the reset link."


@pytest.mark.unit
async def test_mock_json_mode_returns_json_string() -> None:
    client = MockLLMClient(json_response='{"category": "tech"}')
    resp = await client.chat_completion(
        [ChatMessage(role="user", content="classify this")],
        json_mode=True,
    )
    assert resp.text == '{"category": "tech"}'


@pytest.mark.unit
async def test_mock_records_calls_for_inspection() -> None:
    client = MockLLMClient()
    await client.chat_completion(
        [ChatMessage(role="user", content="ping")],
        temperature=0.7,
        request_id="rq-1",
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["temperature"] == 0.7
    assert call["request_id"] == "rq-1"


@pytest.mark.unit
async def test_mock_streaming_yields_chunks_and_finish() -> None:
    client = MockLLMClient(responses={"hi": "alpha beta gamma"})
    chunks: list[ChatCompletionChunk] = []
    async for ch in client.chat_completion_stream(
        [ChatMessage(role="user", content="hi")],
    ):
        chunks.append(ch)
    assert len(chunks) >= 2
    assert "".join(c.delta_text for c in chunks).strip() == "alpha beta gamma"
    assert chunks[-1].finish_reason == "stop"


@pytest.mark.unit
def test_factory_returns_mock_for_provider_mock() -> None:
    settings = Settings()  # из conftest: LLM_PROVIDER=mock
    assert settings.llm.provider == "mock"
    client = create_llm_client(settings)
    assert isinstance(client, MockLLMClient)
    # Совместим с протоколом LLMClient (runtime-проверка).
    assert isinstance(client, LLMClient)


@pytest.mark.unit
def test_factory_raises_on_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    # Подменяем провайдер на не-валидный — pydantic не пропустит при создании,
    # поэтому правим объект напрямую через приватный путь.
    object.__setattr__(settings.llm, "provider", "weird")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_llm_client(settings)


@pytest.mark.unit
def test_factory_yandexgpt_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    object.__setattr__(settings.llm, "provider", "yandexgpt")
    with pytest.raises(NotImplementedError):
        create_llm_client(settings)
