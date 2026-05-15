"""Базовый conftest.

На этапе 0 фикстур по сути нет — модули, для которых они нужны, ещё не
существуют. Пока даём чистый ``Settings`` поверх пустого окружения, чтобы
тесты не зависели от локального `.env`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Изолирует тесты от локального .env и пишет временный пустой файл."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "mock")
    monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "app.db"))
    # CSRF в тестах выключаем по умолчанию: специальный тест ниже включает его
    # явно и проверяет блокировку/пропуск.
    monkeypatch.setenv("SECURITY_CSRF_ENABLED", "false")
    # На случай, если кэш настроек уже инициализирован в другом тесте.
    try:
        from config.settings import reset_settings_cache

        reset_settings_cache()
    except Exception:
        pass
    yield
    try:
        from config.settings import reset_settings_cache

        reset_settings_cache()
    except Exception:
        pass


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """По умолчанию пропускаем тесты с маркером real_llm."""
    if os.getenv("RUN_REAL_LLM"):
        return
    skip_real = pytest.mark.skip(reason="real_llm-тесты пропущены; запустите с RUN_REAL_LLM=1")
    for item in items:
        if "real_llm" in item.keywords:
            item.add_marker(skip_real)
