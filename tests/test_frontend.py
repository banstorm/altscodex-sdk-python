# AltsCodex 프론트엔드 헬퍼 단위 테스트 — URL 빌더와 콜백 파서 검증
"""Unit tests for the server-side frontend helper."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from altscodex import AltsCodex


def test_build_login_url_uses_default_altscodex_url() -> None:
    helper = AltsCodex(client_id="cid", redirect_uri="https://app.example.com/cb")
    login = helper.build_login_url()

    parsed = urlparse(login.url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "altscodex.com"
    assert parsed.path == "/oauth/login"

    params = parse_qs(parsed.query)
    assert params["client_id"] == ["cid"]
    assert params["redirect_uri"] == ["https://app.example.com/cb"]
    assert params["response_type"] == ["code"]
    assert params["state"] == [login.state]


def test_build_login_url_strips_trailing_slash_and_honours_state() -> None:
    helper = AltsCodex(
        client_id="cid",
        redirect_uri="https://app.example.com/cb",
        altscodex_url="https://staging.altscodex.com/",
    )
    login = helper.build_login_url(state="custom-state")
    assert login.state == "custom-state"
    assert login.url.startswith("https://staging.altscodex.com/oauth/login?")


def test_parse_callback_success() -> None:
    payload = AltsCodex.parse_callback(
        {"success": "1", "code": "abc", "state": "s"}
    )
    assert payload.success is True
    assert payload.code == "abc"
    assert payload.state == "s"


def test_parse_callback_failure() -> None:
    payload = AltsCodex.parse_callback({"success": "0", "state": "s"})
    assert payload.success is False
    assert payload.code is None


def test_constructor_validates_required_options() -> None:
    with pytest.raises(ValueError, match="client_id"):
        AltsCodex(client_id="", redirect_uri="x")
    with pytest.raises(ValueError, match="redirect_uri"):
        AltsCodex(client_id="x", redirect_uri="")
