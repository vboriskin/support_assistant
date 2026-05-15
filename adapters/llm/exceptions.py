"""Иерархия исключений LLM-адаптеров.

Все ошибки наследуются от ``LLMError`` — вызывающий код может ловить базовый
класс, если детали неважны (например, в фоновой задаче ингеста — фейл одного
тикета не должен ронять весь пайплайн).
"""

from __future__ import annotations


class LLMError(Exception):
    """Базовая ошибка LLM."""


class LLMAuthError(LLMError):
    """401/403 — проблемы с аутентификацией или истёкшие credentials."""


class LLMRateLimitError(LLMError):
    """429 — превышен лимит. ``retry_after`` — секунды из заголовка, если есть."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMTimeoutError(LLMError):
    """Превышен httpx-таймаут."""


class LLMBadRequestError(LLMError):
    """4xx (кроме 401/429) — некорректный запрос (мы где-то ошиблись)."""


class LLMServerError(LLMError):
    """5xx — ошибка на стороне сервера. Кандидат на retry."""


class LLMResponseParseError(LLMError):
    """Не удалось распарсить ответ (битый JSON, неожиданная структура)."""
