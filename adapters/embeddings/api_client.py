"""Эмбеддинги через HTTP API (OpenAI-совместимый endpoint).

Используется, если в контуре банка появится сервис эмбеддингов с
``POST /embeddings`` — стандартным OpenAI-форматом ответа. Конфигурация —
``EMBEDDINGS_API_URL`` + ``EMBEDDINGS_API_KEY``.
"""

from __future__ import annotations

import httpx

from config.logging import get_logger
from config.settings import Settings
from core.redact import redact_secrets

logger = get_logger("adapters.embeddings.api_client")

_BATCH = 64


class APIEmbeddingsClient:
    def __init__(self, settings: Settings) -> None:
        s = settings.embeddings
        if not s.api_url:
            raise ValueError("EMBEDDINGS_API_URL is not configured for provider=api")
        self._url = s.api_url
        self._api_key = s.api_key.get_secret_value()
        self._model_name = s.model_name
        self._dimension = s.dimension
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), trust_env=False)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def _embed_batch(self, inputs: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = await self._http.post(
                self._url,
                json={"model": self._model_name, "input": inputs},
                headers=headers,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"Embeddings HTTP error: {redact_secrets(str(e))}") from e

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Embeddings API {resp.status_code}: {redact_secrets(resp.text[:200])}"
            )
        data = resp.json()
        return [item["embedding"] for item in data["data"]]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            result.extend(await self._embed_batch(texts[i : i + _BATCH]))
        return result

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_batch([text]))[0]

    async def aclose(self) -> None:
        await self._http.aclose()
