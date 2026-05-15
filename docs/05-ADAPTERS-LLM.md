# 05. Adapters: LLM

Адаптер LLM — самый сложный из всех. Поддерживает несколько провайдеров за единым интерфейсом, обрабатывает аутентификацию (OAuth для GigaChat), retry, streaming.

## Базовый интерфейс

`adapters/llm/base.py`:

```python
from typing import AsyncIterator, Literal, Protocol
from pydantic import BaseModel

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
    raw: dict | None = None              # сырой ответ для отладки

class LLMClient(Protocol):
    """Базовый интерфейс LLM-адаптера."""

    @property
    def model_name(self) -> str: ...

    async def chat_completion(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,         # если LLM поддерживает structured output
        model: str | None = None,        # override default
        request_id: str | None = None,
    ) -> ChatCompletionResponse: ...

    async def chat_completion_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        model: str | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[ChatCompletionChunk]: ...

    async def aclose(self) -> None: ...
```

## Исключения

`adapters/llm/exceptions.py`:

```python
class LLMError(Exception):
    """Базовая ошибка LLM."""

class LLMAuthError(LLMError):
    """401/403 — проблемы с аутентификацией."""

class LLMRateLimitError(LLMError):
    """429 — превышен лимит."""
    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after

class LLMTimeoutError(LLMError):
    """Превышен таймаут."""

class LLMBadRequestError(LLMError):
    """4xx (кроме 401/429) — некорректный запрос."""

class LLMServerError(LLMError):
    """5xx — ошибка на стороне сервера."""

class LLMResponseParseError(LLMError):
    """Не удалось распарсить ответ."""
```

## GigaChat-адаптер

Самая важная реализация. Учитывает все нюансы из боевого опыта (источник: `INTEGRATIONS_PORTING_GUIDE.md` от автора-внутреннего опыта).

### Особенности GigaChat

1. **OAuth 2.0** через `client_credentials`. Токен живёт ~30 минут.
2. **Single-flight refresh** — параллельные запросы не должны делать N OAuth-вызовов. Только один inflight, остальные ждут.
3. **Self-signed SSL** в корп-контуре. `verify=False` или CA-bundle.
4. **`RqUID`** — уникальный идентификатор запроса (UUID), нужен в headers.
5. **OpenAI-совместимый payload** для `/chat/completions`, но не на 100% — иногда отличается.
6. **401-retry**: при 401 — один retry с принудительным обновлением токена.
7. **Streaming** через SSE с heartbeat'ом (корп-прокси режут idle через 60s).

### Реализация

`adapters/llm/gigachat.py`:

```python
import asyncio
import time
import uuid
import json
from typing import AsyncIterator
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import LLMClient, ChatMessage, ChatCompletionChunk, ChatCompletionResponse
from .exceptions import (
    LLMAuthError, LLMRateLimitError, LLMTimeoutError,
    LLMBadRequestError, LLMServerError, LLMResponseParseError,
)
from config.settings import Settings

logger = structlog.get_logger(__name__)


class _TokenCache:
    """Кэш OAuth-токена с single-flight refresh."""
    def __init__(self):
        self._token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task | None = None

    def is_valid(self) -> bool:
        # запас 90 секунд до истечения
        return self._token is not None and time.time() < self._expires_at - 90

    def set(self, token: str, expires_at: float) -> None:
        self._token = token
        self._expires_at = expires_at

    def get(self) -> str | None:
        return self._token


class GigaChatClient:
    """LLM-адаптер для GigaChat (Сбер)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.gc = settings.gigachat
        self._token_cache = _TokenCache()
        # httpx клиент с настройками SSL
        verify: bool | str = self.gc.verify_ssl
        if self.gc.ca_bundle_path:
            verify = str(self.gc.ca_bundle_path)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm.timeout_seconds, connect=10),
            verify=verify,
            trust_env=False,                # не использовать env-proxy без явного указания
        )
        # Single-flight для OAuth
        self._oauth_lock = asyncio.Lock()
        self._oauth_inflight: asyncio.Task | None = None

    @property
    def model_name(self) -> str:
        return self.gc.model_primary

    # ---------- OAuth ----------

    async def _fetch_token(self) -> str:
        """Получить новый OAuth-токен (один вызов на всех ожидающих)."""
        client_id = self.gc.client_id.get_secret_value()
        client_secret = self.gc.client_secret.get_secret_value()
        if not client_id or not client_secret:
            raise LLMAuthError("GigaChat client_id/client_secret not configured")

        rq_uid = str(uuid.uuid4())
        body = {"scope": self.gc.scope}
        try:
            resp = await self._http.post(
                self.gc.auth_url,
                data=body,
                auth=(client_id, client_secret),
                headers={
                    "RqUID": rq_uid,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"OAuth timeout: {e}") from e
        except httpx.HTTPError as e:
            raise LLMServerError(f"OAuth HTTP error: {e}") from e

        if resp.status_code >= 400:
            raise LLMAuthError(
                f"OAuth failed: {resp.status_code} {resp.text[:200]}"
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise LLMResponseParseError(f"OAuth: invalid JSON: {e}") from e

        token = data.get("access_token")
        expires_at_raw = data.get("expires_at")
        if not token:
            raise LLMAuthError("OAuth: no access_token in response")

        # expires_at в миллисекундах согласно GigaChat API
        if isinstance(expires_at_raw, (int, float)):
            expires_at = expires_at_raw / 1000.0
        else:
            expires_at = time.time() + 29 * 60     # дефолт 29 минут

        self._token_cache.set(token, expires_at)
        logger.info("gigachat.oauth.refreshed", expires_in_sec=int(expires_at - time.time()))
        return token

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        """Получить актуальный токен. Single-flight."""
        if not force_refresh and self._token_cache.is_valid():
            return self._token_cache.get()    # type: ignore

        async with self._oauth_lock:
            # double-check после захвата lock
            if not force_refresh and self._token_cache.is_valid():
                return self._token_cache.get()    # type: ignore
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
        json_mode: bool = False,
    ) -> dict:
        payload = {
            "model": model or self.gc.model_primary,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_mode:
            # GigaChat не имеет надёжного JSON mode. Реализуем через промпт-инструкцию.
            # Здесь — placeholder, реальное «JSON-mode» обеспечивается на уровне промпта.
            pass
        return payload

    async def _request_chat(
        self,
        payload: dict,
        *,
        request_id: str | None,
        stream: bool,
    ) -> httpx.Response:
        """Выполняет POST на /chat/completions с авторетраем по 401."""
        url = f"{self.gc.base_url.rstrip('/')}/chat/completions"

        # Первая попытка
        token = await self._get_token()
        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "X-Request-Id": request_id or str(uuid.uuid4()),
            }
            try:
                if stream:
                    resp = await self._http.send(
                        self._http.build_request("POST", url, json=payload, headers=headers),
                        stream=True,
                    )
                else:
                    resp = await self._http.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMTimeoutError(f"Chat timeout: {e}") from e

            if resp.status_code == 401 and attempt == 1:
                # Токен мог истечь — принудительный refresh и retry
                logger.warning("gigachat.401_retry")
                if stream:
                    await resp.aclose()
                token = await self._get_token(force_refresh=True)
                continue

            return resp

        raise LLMAuthError("401 after token refresh")

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
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            model=model,
            json_mode=json_mode,
        )
        resp = await self._request_chat(payload, request_id=request_id, stream=False)

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise LLMRateLimitError(
                f"GigaChat rate limited: {resp.text[:200]}",
                retry_after=int(retry_after) if retry_after else None,
            )
        if resp.status_code >= 500:
            raise LLMServerError(f"GigaChat 5xx: {resp.status_code} {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMBadRequestError(f"GigaChat 4xx: {resp.status_code} {resp.text[:200]}")

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise LLMResponseParseError(f"Invalid JSON in response: {e}") from e

        try:
            text = data["choices"][0]["message"]["content"]
            finish_reason = data["choices"][0].get("finish_reason", "stop")
        except (KeyError, IndexError) as e:
            raise LLMResponseParseError(f"Unexpected response shape: {e}; raw={str(data)[:300]}") from e

        usage = data.get("usage", {})
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
                err_text = await resp.aread()
                if resp.status_code == 429:
                    raise LLMRateLimitError(f"Rate limited: {err_text[:200]}")
                if resp.status_code >= 500:
                    raise LLMServerError(f"GigaChat 5xx: {resp.status_code} {err_text[:200]}")
                raise LLMBadRequestError(f"GigaChat {resp.status_code}: {err_text[:200]}")

            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith(":"):
                    # SSE comment / heartbeat
                    continue
                if not line.startswith("data: "):
                    continue
                payload_str = line[len("data: "):]
                if payload_str == "[DONE]":
                    return
                try:
                    chunk_data = json.loads(payload_str)
                except json.JSONDecodeError:
                    logger.warning("gigachat.stream.bad_chunk", payload=payload_str[:200])
                    continue
                try:
                    delta = chunk_data["choices"][0].get("delta", {}).get("content", "")
                    finish = chunk_data["choices"][0].get("finish_reason")
                except (KeyError, IndexError):
                    continue
                if delta or finish:
                    yield ChatCompletionChunk(delta_text=delta or "", finish_reason=finish)
        finally:
            await resp.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()
```

### Важные детали реализации

**Логирование без секретов.** Никогда не пишем `client_secret`, `client_id`, токен — в логи. Только `request_id` и метаданные.

**Структура логов:**

```python
logger.info(
    "llm.call",
    purpose="answer",
    provider="gigachat",
    model="GigaChat-Max",
    prompt_tokens=usage.get("prompt_tokens"),
    completion_tokens=usage.get("completion_tokens"),
    latency_ms=int((time.time() - t0) * 1000),
    request_id=request_id,
)
```

**Контроль payload size.** Перед отправкой проверяем длину текста. Если превышает лимит модели — обрезаем по токенам (через `tiktoken` для оценки, или эвристически).

**Тонкое место: SSE heartbeat.** В GigaChat корп-прокси режут idle соединение через 60 секунд. Если ответ длинный — SSE-сервер должен слать `: ping\n\n` каждые 25 секунд. Наш клиент должен корректно их игнорировать (см. `if line.startswith(":"): continue`).

## YandexGPT-адаптер

`adapters/llm/yandexgpt.py`. Похожий по структуре, но:
- Аутентификация через API-key (`Authorization: Api-Key <key>`), без OAuth.
- Другой формат payload (`completionOptions`, `messages: [{role, text}]`).
- Streaming через chunked HTTP, не SSE.

Скелет:

```python
class YandexGPTClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.yandex.api_key.get_secret_value()
        self.folder_id = settings.yandex.folder_id
        self.model_uri = settings.yandex.model_uri
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm.timeout_seconds),
            verify=True,
            trust_env=False,
        )

    async def chat_completion(self, messages, ...):
        payload = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens),
            },
            "messages": [{"role": m.role, "text": m.content} for m in messages],
        }
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
            "x-folder-id": self.folder_id,
        }
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        resp = await self._http.post(url, json=payload, headers=headers)
        # ...
```

## OpenAI-compatible адаптер

`adapters/llm/openai_compatible.py`. Тонкая обёртка, нужна для локального запуска с моделью через vLLM/Ollama/LM Studio, а также для интеграции с любыми внутренними шлюзами, выставляющими OpenAI API.

```python
class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.base_url = settings.openai.base_url.rstrip("/")
        self.api_key = settings.openai.api_key.get_secret_value()
        self.model = settings.openai.model
        # ...

    async def chat_completion(self, messages, ...):
        payload = {
            "model": model or self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        # response_format для JSON mode, если поддерживается
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"
        # ...
```

## Mock-адаптер

`adapters/llm/mock.py`. Нужен для тестов и разработки без сети.

```python
class MockLLMClient:
    """Mock-клиент с предопределёнными ответами."""

    def __init__(self, settings: Settings, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self._default = "Mock response. Set MOCK_LLM_RESPONSE env var or pass to constructor."

    async def chat_completion(self, messages, **kwargs) -> ChatCompletionResponse:
        # Поиск по содержанию последнего user-сообщения
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        key = (last_user.content[:100] if last_user else "")[:64]
        text = self.responses.get(key, self._default)
        # Чтобы тесты могли проверять JSON-mode
        if kwargs.get("json_mode"):
            text = '{"mock": true}'
        return ChatCompletionResponse(
            text=text, finish_reason="stop",
            prompt_tokens=10, completion_tokens=20, total_tokens=30,
            model="mock-llm", raw=None,
        )

    async def chat_completion_stream(self, messages, **kwargs):
        resp = await self.chat_completion(messages, **kwargs)
        for word in resp.text.split():
            yield ChatCompletionChunk(delta_text=word + " ")
        yield ChatCompletionChunk(delta_text="", finish_reason="stop")

    async def aclose(self): pass
```

## Factory

```python
# adapters/llm/factory.py
from .base import LLMClient
from .gigachat import GigaChatClient
from .yandexgpt import YandexGPTClient
from .openai_compatible import OpenAICompatibleClient
from .mock import MockLLMClient
from config.settings import Settings

def create_llm_client(settings: Settings) -> LLMClient:
    provider = settings.llm.provider
    if provider == "gigachat":
        return GigaChatClient(settings)
    if provider == "yandexgpt":
        return YandexGPTClient(settings)
    if provider == "openai_compatible":
        return OpenAICompatibleClient(settings)
    if provider == "mock":
        return MockLLMClient(settings)
    raise ValueError(f"Unknown LLM provider: {provider}")
```

## DI в FastAPI

```python
# api/dependencies.py
from functools import lru_cache
from fastapi import Depends
from adapters.llm.factory import create_llm_client
from adapters.llm.base import LLMClient
from config.settings import get_settings

@lru_cache
def llm_client() -> LLMClient:
    return create_llm_client(get_settings())

# В роутах:
async def chat_endpoint(llm: Annotated[LLMClient, Depends(llm_client)]):
    response = await llm.chat_completion([...])
```

Закрытие — в lifespan:

```python
async def lifespan(app: FastAPI):
    yield
    await llm_client().aclose()
```

## Retry с tenacity (опционально)

Для не-streaming вызовов можно обернуть в retry:

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    retry=retry_if_exception_type((LLMServerError, LLMRateLimitError, LLMTimeoutError)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def chat_with_retry(client: LLMClient, messages: list[ChatMessage], **kwargs):
    return await client.chat_completion(messages, **kwargs)
```

Не оборачивать `chat_completion_stream` в retry — streaming retry'ить сложно, лучше падать.

## Безопасность

1. **Whitelist хостов.** LLM-клиент НЕ ходит на произвольные URL. Хосты — только из `.env`. Никаких пользовательских `apiBaseUrl`.
2. **Логирование без секретов.** Перед записью в лог любого error-сообщения — прогон через `redact_secrets()` (regex на `Bearer ***`, `client_secret`, `token=`).
3. **Тайм-ауты на всё.** httpx с явным `Timeout(connect=10, total=60)`. Без бесконечных висящих запросов.
4. **PII в payload.** Перед отправкой в LLM — гарантия, что payload не содержит PII. Это ответственность вызывающего кода (см. `08-PII-MASKING.md`), но в адаптер можно добавить sanity-check log: «warning: payload содержит pattern X».

## Тесты

См. `18-TESTING.md`. Минимум для LLM-адаптера:

- Mock тесты, что `chat_completion` возвращает ожидаемый объект.
- GigaChat: тест OAuth single-flight (5 параллельных вызовов = 1 OAuth-запрос).
- GigaChat: тест 401-retry.
- GigaChat: тест парсинга SSE.
- Тест redact_secrets на error-paths.
