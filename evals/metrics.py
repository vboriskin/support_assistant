"""Чистые арифметические метрики retrieval-качества.

Без LLM — годятся для CI/unit-тестов. Используются runner-ом и могут быть
вызваны независимо.
"""

from __future__ import annotations


def compute_recall_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    """1.0, если хотя бы один из ``expected`` попал в top-K, иначе 0.0.

    Если ``expected`` пуст — кейс «у нас нет ожиданий», считаем 1.0.
    """
    if not expected:
        return 1.0
    if k <= 0 or not retrieved:
        return 0.0
    top = set(retrieved[:k])
    return float(any(eid in top for eid in expected))


def compute_mrr(retrieved: list[str], expected: list[str]) -> float:
    """Mean Reciprocal Rank: 1/r первого попавшего ``expected``."""
    if not expected or not retrieved:
        return 0.0
    expected_set = set(expected)
    for i, rid in enumerate(retrieved, start=1):
        if rid in expected_set:
            return 1.0 / i
    return 0.0


def compute_precision_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    expected_set = set(expected)
    hits = sum(1 for rid in top if rid in expected_set)
    return hits / k
