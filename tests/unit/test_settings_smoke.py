"""Smoke-тест: настройки грузятся и группы доступны.

Этап 0 — проверка того, что каркас собирается. Содержательные тесты
появятся на следующих этапах вместе с соответствующими модулями.
"""

from __future__ import annotations

import pytest

from config.settings import Settings, get_settings


@pytest.mark.unit
def test_settings_load_with_defaults() -> None:
    settings = Settings()
    assert settings.app_env == "local"
    assert settings.db.backend == "sqlite"
    assert settings.llm.provider == "mock"
    assert settings.embeddings.provider == "mock"


@pytest.mark.unit
def test_get_settings_is_singleton() -> None:
    assert get_settings() is get_settings()


@pytest.mark.unit
def test_db_url_sqlite() -> None:
    settings = Settings()
    assert settings.db.url.startswith("sqlite+aiosqlite:///")
    assert settings.db.sync_url.startswith("sqlite:///")
