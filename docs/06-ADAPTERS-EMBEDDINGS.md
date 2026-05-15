# 06. Adapters: Embeddings

Эмбеддинги — векторное представление текста для семантического поиска. Поддерживаем два провайдера: локальные модели (sentence-transformers) и API-клиент (если в контуре окажется доступный сервис эмбеддингов).

## Базовый интерфейс

`adapters/embeddings/base.py`:

```python
from typing import Protocol
import numpy as np

class EmbeddingsClient(Protocol):
    """Базовый интерфейс эмбеддингов."""

    @property
    def dimension(self) -> int: ...

    @property
    def model_name(self) -> str: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинг для документов индекса. Может отличаться от query."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Эмбеддинг для поискового запроса."""
        ...

    async def aclose(self) -> None: ...
```

Многие модели (включая `multilingual-e5`) различают эмбеддинги для **документов** и **запросов** через префиксы. Это важно для качества — отдельные методы помогают не забыть про это.

## Локальная модель: sentence-transformers

`adapters/embeddings/local_st.py`. Используем модель `intfloat/multilingual-e5-large` — мультиязычная, хорошо работает с русским.

```python
import asyncio
import numpy as np
from sentence_transformers import SentenceTransformer
import structlog

from .base import EmbeddingsClient
from config.settings import Settings

logger = structlog.get_logger(__name__)


class LocalSentenceTransformersClient:
    """Локальные эмбеддинги через sentence-transformers."""

    # Префиксы для multilingual-e5
    _DOC_PREFIX = "passage: "
    _QUERY_PREFIX = "query: "

    def __init__(self, settings: Settings):
        s = settings.embeddings
        self._model_name = s.model_name
        self._cache_dir = s.cache_dir
        self._device = s.device
        self._batch_size = s.batch_size
        self._dimension = s.dimension
        self._model: SentenceTransformer | None = None
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def _ensure_model(self) -> SentenceTransformer:
        """Lazy-init модели в отдельном thread (загрузка тяжёлая)."""
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model
            logger.info("embeddings.loading", model=self._model_name, device=self._device)
            loop = asyncio.get_running_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: SentenceTransformer(
                    self._model_name,
                    cache_folder=str(self._cache_dir),
                    device=self._device,
                ),
            )
            actual_dim = self._model.get_sentence_embedding_dimension()
            if actual_dim != self._dimension:
                logger.warning(
                    "embeddings.dimension_mismatch",
                    expected=self._dimension,
                    actual=actual_dim,
                )
                self._dimension = actual_dim
            logger.info("embeddings.loaded", dimension=self._dimension)
            return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = await self._ensure_model()
        prefixed = [self._DOC_PREFIX + t for t in texts]
        # encode синхронный — выполняем в executor
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(
            None,
            lambda: model.encode(
                prefixed,
                batch_size=self._batch_size,
                normalize_embeddings=True,    # cosine = dot product
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
        )
        return vectors.tolist()

    async def embed_query(self, text: str) -> list[float]:
        model = await self._ensure_model()
        loop = asyncio.get_running_loop()
        vector = await loop.run_in_executor(
            None,
            lambda: model.encode(
                [self._QUERY_PREFIX + text],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0],
        )
        return vector.tolist()

    async def aclose(self) -> None:
        # Освобождаем модель из памяти (особенно важно для CUDA)
        if self._model is not None:
            del self._model
            self._model = None
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
```

### Кэширование модели

Веса модели — несколько ГБ. Cache_dir по умолчанию `./models/embeddings`. При первом запуске — скачиваются (если есть интернет). В внутреннем контуре без интернета — скачать локально, положить в репозиторий или в общий volume.

**Pre-download script.** В `scripts/download_models.py`:

```python
"""Скачивает модель эмбеддингов в локальный кэш."""
import sys
from pathlib import Path
from sentence_transformers import SentenceTransformer
from config.settings import get_settings

def main():
    settings = get_settings()
    s = settings.embeddings
    print(f"Downloading {s.model_name} to {s.cache_dir}...")
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(s.model_name, cache_folder=str(s.cache_dir))
    print(f"OK. Dimension: {model.get_sentence_embedding_dimension()}")

if __name__ == "__main__":
    main()
```

Запуск: `python -m scripts.download_models`.

### Производительность

| Параметр | На CPU (i7) | На GPU (T4) |
|---|---|---|
| `multilingual-e5-large` (1024-dim) | ~50–100 текстов/сек | ~500–1000 текстов/сек |
| `multilingual-e5-base` (768-dim) | ~150 текстов/сек | ~2000 текстов/сек |

Для MVP CPU-режим подходит. На большие объёмы (десятки тысяч тикетов в индексе) — можно перейти на CUDA или сменить на `-base` модель.

## API-клиент

`adapters/embeddings/api_client.py`. Если в внутреннем контуре найдётся сервис эмбеддингов (или GigaChat начнёт предоставлять их через свой API) — используем его.

```python
import httpx
from .base import EmbeddingsClient
from config.settings import Settings

class APIEmbeddingsClient:
    """Эмбеддинги через HTTP API.

    Ожидаемый формат запроса (примерно как OpenAI):
        POST {api_url}/embeddings
        body: {"model": "...", "input": ["text1", "text2"]}
        response: {"data": [{"embedding": [...]}, ...]}
    """

    def __init__(self, settings: Settings):
        s = settings.embeddings
        self._url = s.api_url
        self._api_key = s.api_key.get_secret_value() if s.api_key else None
        self._model_name = s.model_name
        self._dimension = s.dimension
        self._http = httpx.AsyncClient(timeout=30.0, verify=False)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    async def _embed_batch(self, inputs: list[str]) -> list[list[float]]:
        if not self._url:
            raise RuntimeError("EMBEDDINGS_API_URL is not configured")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"model": self._model_name, "input": inputs}
        resp = await self._http.post(self._url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Бьём на чанки по 64
        result = []
        for i in range(0, len(texts), 64):
            result.extend(await self._embed_batch(texts[i:i + 64]))
        return result

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_batch([text]))[0]

    async def aclose(self) -> None:
        await self._http.aclose()
```

## Mock

`adapters/embeddings/mock.py` — для тестов без модели:

```python
import hashlib
import numpy as np

class MockEmbeddingsClient:
    """Mock: детерминистические эмбеддинги на основе хеша."""

    def __init__(self, settings):
        self._dimension = settings.embeddings.dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return "mock-embeddings"

    def _vector(self, text: str) -> list[float]:
        # Детерминистический псевдо-вектор: hash → seed → нормализованный vector
        seed = int.from_bytes(hashlib.md5(text.encode()).digest()[:4], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self._dimension).astype(np.float32)
        v = v / np.linalg.norm(v)
        return v.tolist()

    async def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    async def embed_query(self, text):
        return self._vector(text)

    async def aclose(self): pass
```

## Factory

```python
# adapters/embeddings/factory.py
from .base import EmbeddingsClient
from .local_st import LocalSentenceTransformersClient
from .api_client import APIEmbeddingsClient
from .mock import MockEmbeddingsClient
from config.settings import Settings

def create_embeddings_client(settings: Settings) -> EmbeddingsClient:
    provider = settings.embeddings.provider
    if provider == "local":
        return LocalSentenceTransformersClient(settings)
    if provider == "api":
        return APIEmbeddingsClient(settings)
    if provider == "mock":
        return MockEmbeddingsClient(settings)
    raise ValueError(f"Unknown embeddings provider: {provider}")
```

## Использование

```python
embeddings = create_embeddings_client(settings)

# Индексация документов
doc_vectors = await embeddings.embed_documents(["text 1", "text 2"])

# Поиск
query_vector = await embeddings.embed_query("какой-то запрос")
```

## Важные правила

1. **Различайте `embed_documents` и `embed_query`.** Для `multilingual-e5` это критично — без префиксов качество падает заметно.
2. **Нормализуйте векторы.** Так cosine similarity = скалярное произведение, что упрощает поиск в vector store.
3. **Размерность фиксированная.** Указана в `.env`. Проверяется при старте и при индексации. Если меняется модель — нужна полная переиндексация.
4. **Lazy-init модели.** Первый вызов загружает модель в память. Это ~5–10 сек для multilingual-e5-large на CPU.
5. **Кэш на диске.** Чтобы не загружать веса каждый раз — `cache_folder` указывается явно.

## Альтернативные модели

Если `multilingual-e5-large` не устроит:

| Модель | Размерность | Размер на диске | Языки |
|---|---|---|---|
| `intfloat/multilingual-e5-large` (default) | 1024 | ~2.2 GB | 100+ |
| `intfloat/multilingual-e5-base` | 768 | ~1.1 GB | 100+ |
| `intfloat/multilingual-e5-small` | 384 | ~500 MB | 100+ |
| `BAAI/bge-m3` | 1024 | ~2.2 GB | 100+, лучше на технических |
| `cointegrated/rubert-tiny2` | 312 | ~50 MB | RU, очень быстрая |

Для русскоязычной поддержки `multilingual-e5-large` — хороший выбор по умолчанию. Если CPU-перформанс критичен — `-base`. Если есть GPU — `bge-m3` может дать лучшее качество.

## Тесты

См. `18-TESTING.md`. Минимум:

- `embed_documents([])` → `[]`
- `embed_query("...")` → `len(vector) == dimension`
- Нормализация: `np.linalg.norm(vector) ≈ 1.0`
- Различие document vs query: `embed_documents(["x"]) != embed_query("x")` (из-за префиксов)
