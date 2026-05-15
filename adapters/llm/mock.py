"""Mock-реализация ``LLMClient`` для тестов и разработки без сети.

Поведение:
- Ответ выбирается по «ключу» — обычно префикс последнего user-сообщения.
- Если ключа нет в словаре — возвращается ``default_response``.
- Поддерживается ``json_mode``: возвращается валидный JSON-объект
  (по умолчанию ``{"mock": true}``).
- ``chat_completion_stream`` разбивает ответ по словам — это позволяет
  проверить логику клиентского SSE-парсера без сети.

Mock — это часть прод-кода (а не только тестов), потому что план реализации
явно предусматривает работу всего пайплайна на нём при ``LLM_PROVIDER=mock``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from config.settings import Settings

from .base import ChatCompletionChunk, ChatCompletionResponse, ChatMessage


class MockLLMClient:
    """Детерминистический mock-клиент."""

    model_id = "mock-llm"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        responses: dict[str, str] | None = None,
        default_response: str | None = None,
        json_response: str = '{"mock": true}',
    ) -> None:
        self._settings = settings
        self.responses: dict[str, str] = dict(responses or {})
        self._default = default_response or (
            "Mock LLM response. Configure via MockLLMClient(responses=...)."
        )
        self._json_response = json_response
        self.calls: list[dict[str, Any]] = []
        self._closed = False

    @property
    def model_name(self) -> str:
        return self.model_id

    def _pick_response(self, messages: list[ChatMessage], *, json_mode: bool) -> str:
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if last_user is not None:
            for cand, text in self.responses.items():
                if cand in last_user.content or last_user.content.startswith(cand):
                    return text
            key = last_user.content[:64]
            if key in self.responses:
                return self.responses[key]
        # Никакой match в responses — отдаём JSON-заглушку для json-mode и
        # человекочитаемую — иначе. Это позволяет тестам подменять выводы
        # классификаторов и саммаризаторов через responses-словарь, даже когда
        # вызывающий код просит ``json_mode=True``.
        if json_mode:
            return self._json_response
        return self._default

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
        model: str | None = None,
        request_id: str | None = None,
    ) -> ChatCompletionResponse:
        text = self._pick_response(messages, json_mode=json_mode)
        self.calls.append(
            {
                "messages": [m.model_dump() for m in messages],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_mode": json_mode,
                "model": model,
                "request_id": request_id,
            }
        )
        return ChatCompletionResponse(
            text=text,
            finish_reason="stop",
            prompt_tokens=sum(len(m.content) for m in messages) // 4,
            completion_tokens=max(1, len(text) // 4),
            total_tokens=None,
            model=model or self.model_id,
            raw=None,
        )

    async def chat_completion_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        model: str | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]:
        resp = await self.chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            request_id=request_id,
        )
        words = resp.text.split(" ")
        for i, word in enumerate(words):
            suffix = " " if i < len(words) - 1 else ""
            yield ChatCompletionChunk(delta_text=word + suffix)
        yield ChatCompletionChunk(delta_text="", finish_reason="stop")

    async def aclose(self) -> None:
        self._closed = True
