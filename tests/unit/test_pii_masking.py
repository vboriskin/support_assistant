"""Тесты PII-маскирования.

- Прогон golden-набора `tests/fixtures/golden_pii.json` — regex-кейсы всегда,
  NER-кейсы только при доступной Natasha.
- Аудит-словарь (сколько и каких PII заменено).
- Sanity-check в strict-mode для оставшихся email / 16-значных номеров.
- Маскирование ``Ticket`` целиком (``mask_ticket``).
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from config.settings import Settings
from core.models import Ticket, TicketComment
from core.pii.pipeline import PIIMaskingPipeline
from core.pii.ticket_masking import mask_ticket
from core.pii.types import PIIRemainsError

_NATASHA_AVAILABLE = importlib.util.find_spec("natasha") is not None
_GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden_pii.json"


def _settings(ner: bool = False, strict: bool = True) -> Settings:
    s = Settings()
    object.__setattr__(s.pii, "ner_enabled", ner)
    object.__setattr__(s.pii, "strict_mode", strict)
    return s


@pytest.fixture(scope="module")
def golden_cases() -> list[dict]:
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def regex_pipeline() -> PIIMaskingPipeline:
    return PIIMaskingPipeline(_settings(ner=False))


@pytest.fixture
def ner_pipeline() -> PIIMaskingPipeline:
    if not _NATASHA_AVAILABLE:
        pytest.skip("natasha не установлена — NER-кейсы пропущены")
    # Без strict-mode: NER может возвращать частичные совпадения, не наша задача
    # сейчас отлавливать остатки — это ответственность regex-слоя.
    return PIIMaskingPipeline(_settings(ner=True, strict=False))


@pytest.mark.unit
def test_golden_regex_cases_masked_text(
    regex_pipeline: PIIMaskingPipeline, golden_cases: list[dict]
) -> None:
    failures: list[str] = []
    for case in golden_cases:
        if case.get("requires_ner"):
            continue
        if "expected_masked" not in case:
            continue
        result = regex_pipeline.mask(case["input"])
        if result.masked_text != case["expected_masked"]:
            failures.append(
                f"{case['name']}: expected={case['expected_masked']!r} "
                f"got={result.masked_text!r}"
            )
    assert not failures, "\n".join(failures)


@pytest.mark.unit
def test_golden_regex_cases_audit_counts(
    regex_pipeline: PIIMaskingPipeline, golden_cases: list[dict]
) -> None:
    failures: list[str] = []
    for case in golden_cases:
        if case.get("requires_ner"):
            continue
        if "expected_audit" not in case:
            continue
        result = regex_pipeline.mask(case["input"])
        for pii_type, count in case["expected_audit"].items():
            actual = result.audit.get(pii_type, 0)
            if actual != count:
                failures.append(
                    f"{case['name']}: {pii_type} expected={count} got={actual}; "
                    f"masked={result.masked_text!r}"
                )
        # На regex-кейсах PERSON не должен появляться без NER.
        if "PERSON" in result.audit:
            failures.append(f"{case['name']}: неожиданная PERSON в regex-only")
    assert not failures, "\n".join(failures)


@pytest.mark.unit
@pytest.mark.skipif(not _NATASHA_AVAILABLE, reason="natasha не установлена")
def test_golden_ner_cases(
    ner_pipeline: PIIMaskingPipeline, golden_cases: list[dict]
) -> None:
    failures: list[str] = []
    for case in golden_cases:
        if not case.get("requires_ner"):
            continue
        result = ner_pipeline.mask(case["input"])
        if "expected_masked" in case and result.masked_text != case["expected_masked"]:
            failures.append(
                f"{case['name']}: expected={case['expected_masked']!r} "
                f"got={result.masked_text!r}"
            )
        if "expected_audit" in case:
            for pii_type, count in case["expected_audit"].items():
                actual = result.audit.get(pii_type, 0)
                if actual != count:
                    failures.append(
                        f"{case['name']}: {pii_type} expected={count} got={actual}"
                    )
        # Для слабо-предсказуемых случаев (адреса) — допускаем "не меньше N".
        for pii_type, at_least in case.get("expected_audit_at_least", {}).items():
            actual = result.audit.get(pii_type, 0)
            if actual < at_least:
                failures.append(
                    f"{case['name']}: {pii_type} expected>={at_least} got={actual}"
                )
    assert not failures, "\n".join(failures)


@pytest.mark.unit
def test_strict_mode_raises_on_residual_email() -> None:
    pipeline = PIIMaskingPipeline(_settings(ner=False, strict=True))
    # Email-pattern напрямую — будет замаскирован
    assert pipeline.mask("a@b.co").masked_text == "<EMAIL>"
    # Подменяем regex, чтобы он "не нашёл" email — проверяем sanity-check
    pipeline.regex.rules = ()  # type: ignore[misc]
    with pytest.raises(PIIRemainsError):
        pipeline.mask("Не замаскирован: alice@example.com")


@pytest.mark.unit
def test_strict_mode_off_does_not_raise() -> None:
    pipeline = PIIMaskingPipeline(_settings(ner=False, strict=False))
    pipeline.regex.rules = ()  # type: ignore[misc]
    # Должно отработать без исключения
    out = pipeline.mask("alice@example.com")
    assert "alice@example.com" in out.masked_text  # ничего не замаскировано


@pytest.mark.unit
def test_empty_input_is_safe() -> None:
    pipeline = PIIMaskingPipeline(_settings(ner=False))
    assert pipeline.mask("").masked_text == ""
    assert pipeline.mask("").audit == {}


@pytest.mark.unit
def test_mask_ticket_masks_subject_description_and_comments() -> None:
    pipeline = PIIMaskingPipeline(_settings(ner=False))
    ticket = Ticket(
        id="t-1",
        external_id="SM-1",
        channel="email",
        subject="Не загружается выписка, тел. +7 (495) 123-45-67",
        description="Клиент пишет с alice@bank.ru, заявка APP-12345",
        conversation=[
            TicketComment(
                author_role="user",
                content="Карта 4276 1234 5678 9012 заблокирована",
                created_at=datetime(2026, 1, 15, 10, 30),
            ),
            TicketComment(
                author_role="support_l1",
                content="Перезвоните на 8-800-555-35-35",
                created_at=datetime(2026, 1, 15, 10, 35),
            ),
        ],
        status="resolved",
        created_at=datetime(2026, 1, 15, 10, 30),
    )

    masked, audit = mask_ticket(ticket, pipeline)

    assert "<PHONE>" in masked.subject
    assert "<EMAIL>" in masked.description
    assert "<APPLICATION_ID>" in masked.description
    assert "<CARD>" in masked.conversation[0].content
    assert "<PHONE>" in masked.conversation[1].content
    # исходный объект не мутирован
    assert "+7 (495) 123-45-67" in ticket.subject
    # аудит-суммы корректны
    assert audit.get("PHONE", 0) == 2
    assert audit.get("EMAIL", 0) == 1
    assert audit.get("APPLICATION_ID", 0) == 1
    assert audit.get("CARD", 0) == 1
