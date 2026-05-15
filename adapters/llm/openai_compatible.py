"""OpenAI-compatible LLM-адаптер.

Тонкая обёртка над любым сервисом, поднявшим OpenAI-совместимый
``/chat/completions``: vLLM, Ollama, LM Studio, OpenRouter, внутренний банковский
шлюз. Используется для разработки против локальной модели, когда GigaChat
недоступен.

В отличие от GigaChat — без OAuth: статичный ``api_key`` в заголовке.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from config.logging import get_logger
from config.settings import Settings
from core.redact import redact_secrets
from core.security import assert_allowed_llm_host

from .base import ChatCompletionChunk, ChatCompletionResponse, ChatMessage
from .exceptions import (
    LLMAuthError,
    LLMBadRequestError,
    LLMRateLimitError,
    LLMResponseParseError,
    LLMServerError,
    LLMTimeoutError,
)

logger = get_logger("adapters.llm.openai_compatible")


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cfg = settings.openai_compat
        assert_allowed_llm_host(
            self.cfg.base_url,
            extra_hosts=settings.security.allowed_llm_hosts,
        )
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm.timeout_seconds, connect=10.0),
            verify=True,
            trust_env=False,
        )

    @property
    def model_name(self) -> str:
        return self.cfg.model

    def _build_payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        stream: bool,
        model: str | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.cfg.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(self, request_id: str, *, stream: bool) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "X-Request-Id": request_id,
        }

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
        url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
        req_id = request_id or str(uuid.uuid4())
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            model=model,
            json_mode=json_mode,
        )

        t0 = time.time()
        try:
            resp = await self._http.post(url, json=payload, headers=self._headers(req_id, stream=False))
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"Chat timeout: {redact_secrets(str(e))}") from e
        except httpx.HTTPError as e:
            raise LLMServerError(f"Chat HTTP error: {redact_secrets(str(e))}") from e

        self._raise_for_status(resp, body=resp.text)

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise LLMResponseParseError(f"Invalid JSON: {e}") from e

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason", "stop")
        except (KeyError, IndexError, TypeError) as e:
            raise LLMResponseParseError(
                f"Unexpected response shape: {e}; raw={str(data)[:300]}"
            ) from e

        usage = data.get("usage") or {}
        logger.info(
            "llm.call",
            purpose="chat",
            provider="openai_compatible",
            model=data.get("model", payload["model"]),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.time() - t0) * 1000),
            request_id=req_id,
        )
        return ChatCompletionResponse(
            text=text,
            finish_reason=finish_reason,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            model=data.get("model", payload["model"]),
            raw=data,
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
        url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
        req_id = request_id or str(uuid.uuid4())
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            model=model,
            json_mode=False,
        )
        try:
            req = self._http.build_request(
                "POST", url, json=payload, headers=self._headers(req_id, stream=True)
            )
            resp = await self._http.send(req, stream=True)
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"Chat timeout: {redact_secrets(str(e))}") from e
        except httpx.HTTPError as e:
            raise LLMServerError(f"Chat HTTP error: {redact_secrets(str(e))}") from e

        try:
            if resp.status_code != 200:
                err_bytes = await resp.aread()
                self._raise_for_status(resp, body=err_bytes.decode("utf-8", errors="replace"))

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data: "):
                    continue
                payload_str = line[len("data: ") :]
                if payload_str == "[DONE]":
                    return
                try:
                    chunk_data = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                try:
                    choice = chunk_data["choices"][0]
                    delta = choice.get("delta", {}).get("content", "") or ""
                    finish = choice.get("finish_reason")
                except (KeyError, IndexError, TypeError):
                    continue
                if delta or finish:
                    yield ChatCompletionChunk(delta_text=delta, finish_reason=finish)
        finally:
            await resp.aclose()

    def _raise_for_status(self, resp: httpx.Response, *, body: str) -> None:
        if resp.status_code < 400:
            return
        snippet = redact_secrets(body[:200])
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise LLMRateLimitError(
                f"Rate limited: {snippet}",
                retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if resp.status_code in (401, 403):
            raise LLMAuthError(f"Auth error: {resp.status_code} {snippet}")
        if resp.status_code >= 500:
            raise LLMServerError(f"5xx: {resp.status_code} {snippet}")
        raise LLMBadRequestError(f"4xx: {resp.status_code} {snippet}")

    async def aclose(self) -> None:
        await self._http.aclose()
