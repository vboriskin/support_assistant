"""Фабрика embeddings-клиента."""

from __future__ import annotations

from config.settings import Settings

from .base import EmbeddingsClient
from .mock import MockEmbeddingsClient


def create_embeddings_client(settings: Settings) -> EmbeddingsClient:
    provider = settings.embeddings.provider
    if provider == "mock":
        return MockEmbeddingsClient(settings)
    if provider == "local":
        # Импорт здесь — чтобы не тянуть sentence-transformers/torch в окружения,
        # где он не нужен (тесты, mock-режим).
        from .local_st import LocalSentenceTransformersClient

        return LocalSentenceTransformersClient(settings)
    if provider == "api":
        from .api_client import APIEmbeddingsClient

        return APIEmbeddingsClient(settings)
    raise ValueError(f"Unknown embeddings provider: {provider}")
