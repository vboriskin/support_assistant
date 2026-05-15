"""Базовый интерфейс LLM-адаптера и общие модели сообщений/ответов."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionChunk(BaseModel):
    """Один чанк из streaming-ответа."""

    delta_text: str
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """Полный ответ (нестриминговый)."""

    text: str
    finish_reason: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    model: str
    raw: dict[str, Any] | None = Field(default=None, description="сырой ответ для отладки")


@runtime_checkable
class LLMClient(Protocol):
    """Контракт LLM-адаптера.

    Все реализации обязаны быть async-safe — клиент может вызываться из нескольких
    корутин одновременно (например, в Ingest-пайплайне или в FastAPI-обработчиках).
    """

    @property
    def model_name(self) -> str: ...

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
        model: str | None = None,
        request_id: str | None = None,
    ) -> ChatCompletionResponse: ...

    def chat_completion_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        model: str | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]: ...

    async def aclose(self) -> None: ...
