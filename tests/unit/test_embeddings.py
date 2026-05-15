"""Тесты embeddings-адаптера и фабрики.

Здесь — только то, что не требует загрузки настоящей модели:

- размерность, нормализация, детерминизм mock-клиента;
- что фабрика по ``EMBEDDINGS_PROVIDER=mock`` возвращает mock;
- что для незнакомого провайдера фабрика падает явной ошибкой.

Полные интеграционные тесты с настоящей ``multilingual-e5-large`` пойдут под
маркером ``real_embeddings`` (на этапе 7+ при ингесте) — здесь они избыточны
и тормозят CI.
"""

from __future__ import annotations

import math

import pytest

from adapters.embeddings.base import EmbeddingsClient
from adapters.embeddings.factory import create_embeddings_client
from adapters.embeddings.mock import MockEmbeddingsClient
from config.settings import Settings


@pytest.mark.unit
async def test_mock_dimension_matches_settings() -> None:
    settings = Settings()  # из conftest: EMBEDDINGS_PROVIDER=mock, dimension=1024
    client = MockEmbeddingsClient(settings)
    assert client.dimension == settings.embeddings.dimension


@pytest.mark.unit
async def test_mock_embed_query_returns_vector_of_expected_length() -> None:
    client = MockEmbeddingsClient(dimension=128)
    vec = await client.embed_query("test")
    assert isinstance(vec, list)
    assert len(vec) == 128
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.unit
async def test_mock_vectors_are_normalized() -> None:
    client = MockEmbeddingsClient(dimension=256)
    vec = await client.embed_query("payments not working")
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-5


@pytest.mark.unit
async def test_mock_is_deterministic() -> None:
    client = MockEmbeddingsClient(dimension=64)
    a = await client.embed_query("hello world")
    b = await client.embed_query("hello world")
    assert a == b


@pytest.mark.unit
async def test_mock_different_inputs_give_different_vectors() -> None:
    client = MockEmbeddingsClient(dimension=64)
    a = await client.embed_query("alpha")
    b = await client.embed_query("beta")
    assert a != b


@pytest.mark.unit
async def test_mock_embed_documents_empty_returns_empty() -> None:
    client = MockEmbeddingsClient(dimension=64)
    assert await client.embed_documents([]) == []


@pytest.mark.unit
async def test_mock_embed_documents_batch_shape() -> None:
    client = MockEmbeddingsClient(dimension=32)
    out = await client.embed_documents(["one", "two", "three"])
    assert len(out) == 3
    assert all(len(v) == 32 for v in out)


@pytest.mark.unit
async def test_mock_aclose_is_safe() -> None:
    client = MockEmbeddingsClient(dimension=8)
    await client.aclose()


@pytest.mark.unit
def test_factory_returns_mock_for_provider_mock() -> None:
    settings = Settings()
    assert settings.embeddings.provider == "mock"
    client = create_embeddings_client(settings)
    assert isinstance(client, MockEmbeddingsClient)
    assert isinstance(client, EmbeddingsClient)


@pytest.mark.unit
def test_factory_raises_on_unknown_provider() -> None:
    settings = Settings()
    object.__setattr__(settings.embeddings, "provider", "weird")
    with pytest.raises(ValueError, match="Unknown embeddings provider"):
        create_embeddings_client(settings)


@pytest.mark.unit
def test_factory_api_requires_url() -> None:
    settings = Settings()
    object.__setattr__(settings.embeddings, "provider", "api")
    # api_url остаётся None из дефолтов — фабрика должна аккуратно упасть.
    with pytest.raises(ValueError, match="EMBEDDINGS_API_URL"):
        create_embeddings_client(settings)
