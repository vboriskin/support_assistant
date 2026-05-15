"""Загрузка промпт-файлов.

Все промпты лежат как ``.txt`` рядом с этим модулем. Few-shot примеры —
в подкаталоге ``few_shot/``. Подстановка переменных делается через стандартный
``str.format()`` в вызывающем коде; loader сам форматирование не выполняет,
чтобы не прятать ошибки незаданных переменных.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
FEW_SHOT_DIR = PROMPTS_DIR / "few_shot"


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    """Загружает текст ``.txt``-промпта по короткому имени.

    Например, ``load_prompt("system_assistant")`` читает ``system_assistant.txt``.
    Кэшируется в памяти процесса: промпты неизменяемые в рамках одного процесса.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {name} (looked at {path})")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=32)
def load_few_shot(name: str) -> list[dict]:
    """Загружает few-shot набор ``few_shot/<name>.json``."""
    path = FEW_SHOT_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Few-shot bundle not found: {name} (looked at {path})")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Few-shot {name}.json must be a JSON array, got {type(data).__name__}")
    return data


def clear_prompt_cache() -> None:
    """Сбрасывает кэш загрузчиков — нужно в тестах после правки файлов."""
    load_prompt.cache_clear()
    load_few_shot.cache_clear()
