"""Тесты загрузки и целостности промптов.

Что проверяем:

- все 8 промптов из плана грузятся, непустые;
- ``load_prompt("nonexistent")`` бросает ``FileNotFoundError``;
- few-shot bundles грузятся и имеют ожидаемую структуру;
- декларированные placeholders каждого промпта присутствуют в файле и
  безопасно подставляются через ``str.format`` (никаких незаэскейпленных
  ``{...}`` в JSON-литералах примеров).
"""

from __future__ import annotations

import pytest

from core.prompts.loader import (
    clear_prompt_cache,
    load_few_shot,
    load_prompt,
)

EXPECTED_PROMPTS: list[tuple[str, set[str]]] = [
    ("system_assistant", set()),
    ("system_ingest", set()),
    ("ticket_resolution_classifier", {"ticket_text"}),
    ("ticket_summary", {"ticket_text"}),
    ("categorization", {"modules", "subject", "description", "channel", "author_role"}),
    ("reranker", {"top_k", "query", "snippets"}),
    ("judge_faithfulness", {"sources", "answer"}),
    ("judge_helpfulness", {"query", "answer", "expected_summary"}),
]


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_prompt_cache()


@pytest.mark.unit
def test_load_prompt_returns_non_empty_string() -> None:
    text = load_prompt("system_assistant")
    assert isinstance(text, str)
    assert text.strip()
    assert "ассистент" in text.lower()


@pytest.mark.unit
def test_load_prompt_unknown_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("does_not_exist_xyz")


@pytest.mark.unit
@pytest.mark.parametrize("name, placeholders", EXPECTED_PROMPTS)
def test_all_documented_prompts_loadable(name: str, placeholders: set[str]) -> None:
    text = load_prompt(name)
    assert text.strip(), f"{name}.txt is empty"
    for ph in placeholders:
        assert "{" + ph + "}" in text, f"{name}: placeholder {{{ph}}} not found"


@pytest.mark.unit
@pytest.mark.parametrize("name, placeholders", EXPECTED_PROMPTS)
def test_prompt_safely_formats_with_known_placeholders(
    name: str, placeholders: set[str]
) -> None:
    """JSON-литералы внутри промпта должны быть заэскейплены (``{{`` / ``}}``).

    Если бы это было не так, ``str.format`` с правильными переменными всё равно
    падал бы на оставшихся фигурных скобках — то есть тест отлавливает забытые
    эскейпы.
    """
    text = load_prompt(name)
    values = {ph: f"<{ph}>" for ph in placeholders}
    out = text.format(**values)
    for ph in placeholders:
        assert f"<{ph}>" in out


@pytest.mark.unit
def test_few_shot_assistant_examples_have_user_and_assistant_keys() -> None:
    examples = load_few_shot("assistant_examples")
    assert len(examples) >= 2
    for ex in examples:
        assert set(ex.keys()) == {"user", "assistant"}
        assert ex["user"].strip()
        assert ex["assistant"].strip()


@pytest.mark.unit
def test_few_shot_summary_examples_have_required_keys() -> None:
    examples = load_few_shot("summary_examples")
    assert len(examples) >= 1
    required_output = {
        "summary_one_line",
        "symptom",
        "root_cause",
        "solution_steps",
        "affected_module",
        "user_role",
        "is_known_issue",
    }
    for ex in examples:
        assert {"input", "output"} <= set(ex.keys())
        assert ex["input"].strip()
        assert required_output <= set(ex["output"].keys())


@pytest.mark.unit
def test_load_few_shot_unknown_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_few_shot("does_not_exist")


@pytest.mark.unit
def test_load_prompt_is_cached() -> None:
    """Дважды вызываем — те же байты возвращаются из кэша."""
    a = load_prompt("system_assistant")
    b = load_prompt("system_assistant")
    assert a is b  # из lru_cache
