"""Тесты ``core.redact.redact_secrets``."""

from __future__ import annotations

import pytest

from core.redact import redact_secrets


@pytest.mark.unit
def test_redact_bearer_token() -> None:
    s = "Failed request: Authorization: Bearer eyJhbGc.payload.signature foo"
    out = redact_secrets(s)
    assert "eyJhbGc" not in out
    assert "Bearer ***" in out


@pytest.mark.unit
def test_redact_access_token_url_param() -> None:
    s = "https://api.example/path?access_token=abc123XYZ&other=ok"
    out = redact_secrets(s)
    assert "abc123XYZ" not in out
    assert "access_token=***" in out


@pytest.mark.unit
def test_redact_refresh_token_param() -> None:
    s = "POST /token refresh_token=longvalue123 status=200"
    out = redact_secrets(s)
    assert "longvalue123" not in out
    assert "refresh_token=***" in out


@pytest.mark.unit
def test_redact_api_key_param() -> None:
    s = "url?api_key=sk-abc-123-def_456"
    out = redact_secrets(s)
    assert "sk-abc-123-def_456" not in out
    assert "api_key=***" in out


@pytest.mark.unit
def test_redact_json_secret_field() -> None:
    s = '{"client_secret": "topsecret-1234", "user": "alice"}'
    out = redact_secrets(s)
    assert "topsecret-1234" not in out
    assert '"<redacted>": "***"' in out


@pytest.mark.unit
def test_redact_long_token_heuristic() -> None:
    long_tok = "A" * 50
    out = redact_secrets(f"raw error {long_tok} end")
    assert long_tok not in out
    assert "<long_token>" in out


@pytest.mark.unit
def test_redact_keeps_normal_text() -> None:
    s = "Plain message with short id abc123"
    assert redact_secrets(s) == s


@pytest.mark.unit
def test_redact_empty_and_none_safe() -> None:
    assert redact_secrets("") == ""
