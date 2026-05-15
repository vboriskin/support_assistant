"""Фабрика LLM-клиента по конфигу."""

from __future__ import annotations

from config.settings import Settings

from .base import LLMClient
from .gigachat import GigaChatClient
from .mock import MockLLMClient
from .openai_compatible import OpenAICompatibleClient


def create_llm_client(settings: Settings) -> LLMClient:
    """Возвращает LLM-клиент в соответствии с ``settings.llm.provider``.

    Поддерживаемые провайдеры: ``mock``, ``gigachat``, ``openai_compatible``.
    ``yandexgpt`` зарезервирован, но пока не реализован — будет добавлен
    в одном из следующих этапов.
    """
    provider = settings.llm.provider
    if provider == "mock":
        return MockLLMClient(settings)
    if provider == "gigachat":
        return GigaChatClient(settings)
    if provider == "openai_compatible":
        return OpenAICompatibleClient(settings)
    if provider == "yandexgpt":
        raise NotImplementedError(
            "YandexGPT-адаптер пока не реализован; используйте gigachat | openai_compatible | mock"
        )
    raise ValueError(f"Unknown LLM provider: {provider}")
