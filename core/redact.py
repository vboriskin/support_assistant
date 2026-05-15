"""Маскирование секретов в строках для безопасного логирования.

Применяется в адаптерах LLM/HTTP перед записью error-сообщений в лог.
Спецификация — ``docs/19-SECURITY.md`` §"Redact_secrets".

Замечание про "длинный токен": правило в конце списка маскирует любые
непрерывные последовательности из >=40 символов алфавита base64url. Это
агрессивная, но безопасная эвристика: текст ошибки от провайдера почти никогда
не содержит таких длинных «слов», а вот JWT/refresh-токены — содержат.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"access_token=[\w.\-]+", re.IGNORECASE), "access_token=***"),
    (re.compile(r"refresh_token=[\w.\-]+", re.IGNORECASE), "refresh_token=***"),
    (re.compile(r"api[_-]?key=[\w.\-]+", re.IGNORECASE), "api_key=***"),
    (
        re.compile(
            r'"(?:client_secret|password|token|api_key|secret|authorization)"'
            r'\s*:\s*"[^"]+"',
            re.IGNORECASE,
        ),
        '"<redacted>": "***"',
    ),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "<long_token>"),
)


def redact_secrets(s: str) -> str:
    """Заменяет узнаваемые секреты на плейсхолдеры.

    Безопасна для произвольных входных строк. Не выбрасывает исключений.
    """
    if not s:
        return s
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s
