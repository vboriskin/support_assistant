"""Кросс-cutting security-утилиты.

Содержимое — небольшое, но критичное:

- :func:`is_allowed_llm_host` — whitelist хостов для LLM-адаптеров. Если в
  ``.env`` подменят ``GIGACHAT_BASE_URL=https://evil.com`` — клиент откажется
  стартовать. Дополнительные внутренние хосты задаются через
  ``SECURITY_ALLOWED_LLM_HOSTS`` (comma-separated).
- :func:`safe_upload_path` — безопасный путь для пользовательского upload.
  Срезает любые ``../``, оставляет только базовое имя, разрешает
  ``[A-Za-z0-9._-]``, добавляет уникальный префикс.
- :func:`generate_csrf_token` / :func:`verify_csrf_token` —
  in-memory CSRF-токен per ``user_id``. На масштабе нужно перевести на Redis,
  для MVP — словарь в памяти. ``compare_digest`` — защита от timing-атак.
"""

from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# LLM host whitelist
# ---------------------------------------------------------------------------

# Жёстко зашитые «корпоративные» хосты. Не зависят от .env.
ALLOWED_LLM_HOSTS_DEFAULT: frozenset[str] = frozenset(
    {
        "gigachat.devices.sberbank.ru",
        "ngw.devices.sberbank.ru",
        "llm.api.cloud.yandex.net",
    }
)

# Локальные хосты — для разработки/локального запуска OpenAI-совместимой модели.
_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def is_allowed_llm_host(url: str, *, extra_hosts: str = "") -> bool:
    """True, если ``url`` указывает на разрешённый LLM-хост.

    ``extra_hosts`` — comma-separated whitelist из настроек банка/контура.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in ALLOWED_LLM_HOSTS_DEFAULT:
        return True
    if host in _LOCAL_HOSTS:
        return True
    if extra_hosts:
        extra = {h.strip().lower() for h in extra_hosts.split(",") if h.strip()}
        if host in extra:
            return True
    return False


def assert_allowed_llm_host(url: str, *, extra_hosts: str = "") -> None:
    """Бросает ``ValueError``, если хост не в whitelist."""
    if not is_allowed_llm_host(url, extra_hosts=extra_hosts):
        raise ValueError(
            f"LLM host not in whitelist: {url}. "
            f"Add it to SECURITY_ALLOWED_LLM_HOSTS if intended."
        )


# ---------------------------------------------------------------------------
# Safe upload path
# ---------------------------------------------------------------------------


def safe_upload_path(filename: str, upload_dir: Path) -> Path:
    """Возвращает безопасный путь под пользовательский upload.

    - Берём только базовое имя (``Path(filename).name``) — это уже срезает
      ``../`` и абсолютные пути.
    - Заменяем все символы, кроме ``[A-Za-z0-9._-]``, на ``_``.
    - Добавляем уникальный hex-префикс (8 символов).
    - Итог: ``<upload_dir>/<8hex>_<clean>``.
    """
    base = Path(filename or "").name or "upload"
    clean = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base)
    return upload_dir / f"{uuid.uuid4().hex[:8]}_{clean}"


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

_csrf_tokens: dict[str, str] = {}


def generate_csrf_token(user_id: str) -> str:
    """Возвращает существующий или новый токен для ``user_id``."""
    tok = _csrf_tokens.get(user_id)
    if tok is None:
        tok = secrets.token_urlsafe(32)
        _csrf_tokens[user_id] = tok
    return tok


def verify_csrf_token(user_id: str, token: str) -> bool:
    expected = _csrf_tokens.get(user_id)
    if not expected or not token:
        return False
    return secrets.compare_digest(expected, token)


def reset_csrf_store() -> None:
    """Только для тестов."""
    _csrf_tokens.clear()
