"""Unit-тесты ``core.security``."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.security import (
    assert_allowed_llm_host,
    generate_csrf_token,
    is_allowed_llm_host,
    reset_csrf_store,
    safe_upload_path,
    verify_csrf_token,
)

# ----------------------- LLM host whitelist ---------------------------------


@pytest.mark.unit
def test_default_hosts_allowed() -> None:
    assert is_allowed_llm_host("https://gigachat.devices.sberbank.ru/api/v1")
    assert is_allowed_llm_host("https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
    assert is_allowed_llm_host("https://llm.api.cloud.yandex.net/foundationModels/v1/completion")


@pytest.mark.unit
def test_localhost_allowed_for_dev() -> None:
    assert is_allowed_llm_host("http://localhost:8080/v1")
    assert is_allowed_llm_host("http://127.0.0.1:8080")


@pytest.mark.unit
def test_unknown_host_rejected() -> None:
    assert not is_allowed_llm_host("https://evil.example.com")


@pytest.mark.unit
def test_extra_hosts_from_settings() -> None:
    extra = "internal-gw.bank.local, llm.contour"
    assert is_allowed_llm_host("https://internal-gw.bank.local/v1", extra_hosts=extra)
    assert is_allowed_llm_host("https://llm.contour", extra_hosts=extra)


@pytest.mark.unit
def test_assert_raises_for_unknown_host() -> None:
    with pytest.raises(ValueError, match="not in whitelist"):
        assert_allowed_llm_host("https://evil.example.com")


# ----------------------- safe_upload_path -----------------------------------


@pytest.mark.unit
def test_safe_upload_path_strips_traversal(tmp_path: Path) -> None:
    p = safe_upload_path("../../etc/passwd", tmp_path)
    assert p.parent == tmp_path
    # «passwd» уцелело, но без traversal
    assert "passwd" in p.name
    assert ".." not in p.name


@pytest.mark.unit
def test_safe_upload_path_strips_special_chars(tmp_path: Path) -> None:
    p = safe_upload_path("evil; rm -rf /.csv", tmp_path)
    # пробелы и точки с запятой стали `_`
    assert ";" not in p.name
    assert "/" not in p.name
    assert p.name.endswith(".csv")


@pytest.mark.unit
def test_safe_upload_path_empty_filename(tmp_path: Path) -> None:
    p = safe_upload_path("", tmp_path)
    assert p.parent == tmp_path
    assert "upload" in p.name


@pytest.mark.unit
def test_safe_upload_path_unique_prefix(tmp_path: Path) -> None:
    a = safe_upload_path("data.csv", tmp_path)
    b = safe_upload_path("data.csv", tmp_path)
    assert a != b  # уникальный hex-префикс


# ----------------------- CSRF -----------------------------------------------


@pytest.mark.unit
def test_csrf_generate_and_verify() -> None:
    reset_csrf_store()
    tok = generate_csrf_token("alice")
    assert verify_csrf_token("alice", tok) is True


@pytest.mark.unit
def test_csrf_wrong_token_rejected() -> None:
    reset_csrf_store()
    generate_csrf_token("alice")
    assert verify_csrf_token("alice", "wrong") is False


@pytest.mark.unit
def test_csrf_other_user_rejected() -> None:
    reset_csrf_store()
    tok = generate_csrf_token("alice")
    assert verify_csrf_token("bob", tok) is False


@pytest.mark.unit
def test_csrf_token_stable_per_user() -> None:
    reset_csrf_store()
    a = generate_csrf_token("alice")
    b = generate_csrf_token("alice")
    assert a == b
