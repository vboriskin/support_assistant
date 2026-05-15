"""LLM-адаптеры рейзят на хосте вне whitelist."""

from __future__ import annotations

import pytest

from adapters.llm.gigachat import GigaChatClient
from adapters.llm.openai_compatible import OpenAICompatibleClient
from config.settings import Settings


@pytest.mark.unit
def test_gigachat_rejects_unknown_host() -> None:
    s = Settings()
    object.__setattr__(s.gigachat, "base_url", "https://evil.example.com/api/v1")
    with pytest.raises(ValueError, match="not in whitelist"):
        GigaChatClient(s)


@pytest.mark.unit
def test_gigachat_rejects_unknown_auth_host() -> None:
    s = Settings()
    object.__setattr__(s.gigachat, "auth_url", "https://attacker.tld/oauth")
    with pytest.raises(ValueError, match="not in whitelist"):
        GigaChatClient(s)


@pytest.mark.unit
def test_gigachat_accepts_default_sber_hosts() -> None:
    s = Settings()
    # default URLs соответствуют sberbank.ru — должно пройти.
    GigaChatClient(s)


@pytest.mark.unit
def test_openai_compat_rejects_unknown_host() -> None:
    s = Settings()
    object.__setattr__(s.openai_compat, "base_url", "https://evil.example.com/v1")
    with pytest.raises(ValueError, match="not in whitelist"):
        OpenAICompatibleClient(s)


@pytest.mark.unit
def test_openai_compat_accepts_localhost() -> None:
    s = Settings()
    # default openai_compat.base_url — http://localhost:8080/v1
    OpenAICompatibleClient(s)


@pytest.mark.unit
def test_openai_compat_accepts_extra_host() -> None:
    s = Settings()
    object.__setattr__(s.openai_compat, "base_url", "https://internal-gw.bank.local/v1")
    object.__setattr__(s.security, "allowed_llm_hosts", "internal-gw.bank.local")
    OpenAICompatibleClient(s)
