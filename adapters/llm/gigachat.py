"""GigaChat (Сбер) LLM-адаптер.

Особенности, учтённые здесь:

1. **OAuth 2.0 client_credentials.** Токен живёт ~30 минут, кэшируется в памяти
   с запасом 90 секунд до истечения.
2. **Single-flight refresh.** Под `asyncio.Lock` — параллельные запросы не
   делают N OAuth-вызовов.
3. **401-retry.** При 401 — один retry с принудительным обновлением токена.
4. **SSE с heartbeat.** Строки, начинающиеся с ``:``, игнорируем.
5. **Self-signed SSL.** ``verify_ssl=false`` или путь к CA-bundle.
6. **Секреты в логах.** Все error-сообщения проходят через ``redact_secrets``.

Спецификация — ``docs/05-ADAPTERS-LLM.md``.
"""

from __future__ import annotations

import asyncio
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

logger = get_logger("adapters.llm.gigachat")


class _TokenCache:
    """OAuth-токен с пометкой времени истечения."""

    __slots__ = ("_token", "_expires_at")

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    def is_valid(self) -> bool:
        return self._token is not None and time.time() < self._expires_at - 90

    def set(self, token: str, expires_at: float) -> None:
        self._token = token
        self._expires_at = expires_at

    def get(self) -> str | None:
        return self._token


class GigaChatClient:
    """LLM-адаптер для GigaChat."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.gc = settings.gigachat
        extra = settings.security.allowed_llm_hosts
        assert_allowed_llm_host(self.gc.base_url, extra_hosts=extra)
        assert_allowed_llm_host(self.gc.auth_url, extra_hosts=extra)
        self._token_cache = _TokenCache()
        verify: bool | str = self.gc.verify_ssl
        if self.gc.ca_bundle_path:
            verify = str(self.gc.ca_bundle_path)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm.timeout_seconds, connect=10.0),
            verify=verify,
            trust_env=False,
        )
        self._oauth_lock = asyncio.Lock()

    @property
    def model_name(self) -> str:
        return self.gc.model_primary

    # ---------- OAuth ----------

    async def _fetch_token(self) -> str:
        client_id = self.gc.client_id.get_secret_value()
        client_secret = self.gc.client_secret.get_secret_value()
        if not client_id or not client_secret:
            raise LLMAuthError("GigaChat client_id/client_secret not configured")

        rq_uid = str(uuid.uuid4())
        try:
            resp = await self._http.post(
                self.gc.auth_url,
                data={"scope": self.gc.scope},
                auth=(client_id, client_secret),
                headers={
                    "RqUID": rq_uid,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"OAuth timeout: {redact_secrets(str(e))}") from e
        except httpx.HTTPError as e:
            raise LLMServerError(f"OAuth HTTP error: {redact_secrets(str(e))}") from e

        if resp.status_code >= 400:
            body = redact_secrets(resp.text[:200])
            raise LLMAuthError(f"OAuth failed: {resp.status_code} {body}")

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise LLMResponseParseError(f"OAuth: invalid JSON: {e}") from e

        token = data.get("access_token")
        if not token:
            raise LLMAuthError("OAuth: no access_token in response")

        expires_at_raw = data.get("expires_at")
        if isinstance(expires_at_raw, (int, float)):
            # GigaChat возвращает unix-ms
            expires_at = float(expires_at_raw) / 1000.0
        else:
            expires_at = time.time() + 29 * 60

        self._token_cache.set(token, expires_at)
        logger.info(
            "gigachat.oauth.refreshed",
            expires_in_sec=int(expires_at - time.time()),
            rq_uid=rq_uid,
        )
        return token

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_cache.is_valid():
            tok = self._token_cache.get()
            assert tok is not None
            return tok

        async with self._oauth_lock:
            # double-check после захвата lock — single-flight
            if not force_refresh and self._token_cache.is_valid():
                tok = self._token_cache.get()
                assert tok is not None
                return tok
            return await self._fetch_token()

    # ---------- Chat completion ----------

    def _build_payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        stream: bool,
        model: str | None,
    ) -> dict[str, Any]:
        return {
            "model": model or self.gc.model_primary,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    async def _request_chat(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None,
        stream: bool,
    ) -> httpx.Response:
        url = f"{self.gc.base_url.rstrip('/')}/chat/completions"
        req_id = request_id or str(uuid.uuid4())

        token = await self._get_token()
        last_resp: httpx.Response | None = None
        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "X-Request-Id": req_id,
            }
            try:
                if stream:
                    req = self._http.build_request("POST", url, json=payload, headers=headers)
                    resp = await self._http.send(req, stream=True)
                else:
                    resp = await self._http.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMTimeoutError(f"Chat timeout: {redact_secrets(str(e))}") from e
            except httpx.HTTPError as e:
                raise LLMServerError(f"Chat HTTP error: {redact_secrets(str(e))}") from e

            if resp.status_code == 401 and attempt == 1:
                logger.warning("gigachat.401_retry", request_id=req_id)
                if stream:
                    await resp.aclose()
                token = await self._get_token(force_refresh=True)
                continue

            return resp

        # До сюда мы не доходим — цикл всегда либо возвращает, либо continue→return.
        assert last_resp is None
        raise LLMAuthError("401 after forced token refresh")

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
        # json_mode у GigaChat ненадёжен — обеспечивается на уровне промпта.
        _ = json_mode
        t0 = time.time()
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            model=model,
        )
        resp = await self._request_chat(payload, request_id=request_id, stream=False)
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
            provider="gigachat",
            model=data.get("model", payload["model"]),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.time() - t0) * 1000),
            request_id=request_id,
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
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            model=model,
        )
        resp = await self._request_chat(payload, request_id=request_id, stream=True)

        try:
            if resp.status_code != 200:
                err_bytes = await resp.aread()
                err_text = redact_secrets(err_bytes.decode("utf-8", errors="replace")[:300])
                if resp.status_code == 429:
                    raise LLMRateLimitError(f"Rate limited: {err_text}")
                if resp.status_code in (401, 403):
                    raise LLMAuthError(f"Auth error: {err_text}")
                if resp.status_code >= 500:
                    raise LLMServerError(f"GigaChat 5xx: {resp.status_code} {err_text}")
                raise LLMBadRequestError(f"GigaChat {resp.status_code}: {err_text}")

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith(":"):
                    # SSE heartbeat
                    continue
                if not line.startswith("data: "):
                    continue
                payload_str = line[len("data: ") :]
                if payload_str == "[DONE]":
                    return
                try:
                    chunk_data = json.loads(payload_str)
                except json.JSONDecodeError:
                    logger.warning(
                        "gigachat.stream.bad_chunk",
                        payload=redact_secrets(payload_str[:200]),
                    )
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
                f"GigaChat rate limited: {snippet}",
                retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if resp.status_code in (401, 403):
            raise LLMAuthError(f"GigaChat auth error: {resp.status_code} {snippet}")
        if resp.status_code >= 500:
            raise LLMServerError(f"GigaChat 5xx: {resp.status_code} {snippet}")
        raise LLMBadRequestError(f"GigaChat 4xx: {resp.status_code} {snippet}")

    async def aclose(self) -> None:
        await self._http.aclose()
