"""Тесты числовых retrieval-метрик."""

from __future__ import annotations

import pytest

from evals.metrics import compute_mrr, compute_precision_at_k, compute_recall_at_k


@pytest.mark.unit
def test_recall_hits_in_top_k() -> None:
    assert compute_recall_at_k(["a", "b", "c"], ["b"], k=5) == 1.0


@pytest.mark.unit
def test_recall_miss_outside_top_k() -> None:
    assert compute_recall_at_k(["a", "b", "c", "d", "e", "X"], ["X"], k=5) == 0.0


@pytest.mark.unit
def test_recall_empty_expected_is_one() -> None:
    # Пустые ожидания — нечего «не найти», считаем удачей.
    assert compute_recall_at_k(["a"], [], k=5) == 1.0


@pytest.mark.unit
def test_recall_empty_retrieved_is_zero() -> None:
    assert compute_recall_at_k([], ["a"], k=5) == 0.0


@pytest.mark.unit
def test_mrr_first_position() -> None:
    assert compute_mrr(["a", "b", "c"], ["a"]) == 1.0


@pytest.mark.unit
def test_mrr_second_position() -> None:
    assert compute_mrr(["x", "a", "b"], ["a"]) == pytest.approx(0.5)


@pytest.mark.unit
def test_mrr_no_match_is_zero() -> None:
    assert compute_mrr(["x", "y"], ["a"]) == 0.0


@pytest.mark.unit
def test_precision_at_k_basic() -> None:
    # 2 хита в топ-3 из ['a','b','c']
    assert compute_precision_at_k(["a", "b", "c"], ["a", "b"], k=3) == pytest.approx(2 / 3)


@pytest.mark.unit
def test_precision_at_k_zero_k() -> None:
    assert compute_precision_at_k(["a"], ["a"], k=0) == 0.0
