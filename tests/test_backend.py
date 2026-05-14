# AltsCodexBackend 단위 테스트 — 원본 Jest 6 시나리오의 Python 포트
"""Unit tests for ``AltsCodexBackend``.

Ports the six Jest scenarios from ``packages/sdk/__tests__/backend.test.js``
to ``pytest-asyncio`` and ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, List, Mapping

import httpx
import pytest

from altscodex import AltsCodexBackend
from altscodex.exceptions import (
    AuthorizeCallbackTimeoutError,
    AuthorizeFailedError,
    AuthorizeRejectedError,
)


SDK_OPTIONS = {
    "auth_server_url": "http://auth.example.com",
    "client_id": "test-client",
    "client_secret": "test-secret",
    "redirect_uri": "http://localhost:3000/callback",
}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def make_sdk(handler: Callable[[httpx.Request], httpx.Response]) -> AltsCodexBackend:
    """Construct an SDK whose HTTP client routes through a MockTransport."""

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return AltsCodexBackend(**SDK_OPTIONS, http_client=client)


def fake_request(query: Mapping[str, Any]) -> Any:
    """Build a request stand-in compatible with handle_callback."""

    return {"query": dict(query)}


def json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


# ---------------------------------------------------------------------------
# Scenarios — ported from backend.test.js
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_1_happy_path() -> None:
    """authorize → callback → get_token → resolve."""

    captured_state: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/authorize" in request.url.path:
            captured_state.append(request.url.params["state"])
            return json_response({"success": True, "data": "JWT_TOKEN"})
        if "/get_token" in request.url.path:
            return json_response(
                {
                    "id": "slot-1",
                    "access_token": "token",
                    "content_address": "addr",
                    "token_nickname": "nick",
                    "tr_cnt": 0,
                    "code": "code",
                }
            )
        return json_response({}, status_code=404)

    sdk = make_sdk(handler)
    try:
        task = asyncio.create_task(sdk.get_slot_info("JWT_TOKEN"))
        await asyncio.sleep(0.05)

        assert captured_state, "authorize must be dispatched before callback"
        ack = await sdk.handle_callback(
            fake_request({"success": "1", "code": "test-code", "state": captured_state[0]})
        )
        assert ack == {"received": True}

        result = await task
        assert result == {
            "id": "slot-1",
            "access_token": "token",
            "content_address": "addr",
            "token_nickname": "nick",
            "tr_cnt": 0,
            "code": "code",
        }
    finally:
        await sdk.shutdown()


@pytest.mark.asyncio
async def test_scenario_2_expired_token_short_circuits() -> None:
    """When /authorize returns success=false, get_slot_info rejects without callback."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/authorize" in request.url.path:
            return json_response(
                {"success": False, "code": "EXPIRED_TOKEN", "msg": "만료된 토큰"}
            )
        return json_response({}, status_code=404)

    sdk = make_sdk(handler)
    try:
        with pytest.raises(AuthorizeFailedError) as excinfo:
            await sdk.get_slot_info("EXPIRED_JWT")
        assert "EXPIRED_TOKEN" in str(excinfo.value)
        assert excinfo.value.code == "EXPIRED_TOKEN"
        # 메모리 누수 방지: 거부 처리된 state 가 map 에 남으면 안 됨
        assert len(sdk._pending_by_state) == 0
    finally:
        await sdk.shutdown()


@pytest.mark.asyncio
async def test_scenario_3_timeout() -> None:
    """If the callback never arrives, the request rejects after `timeout` seconds."""

    pending: List[asyncio.Future[httpx.Response]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        # authorize never resolves; we hold it open forever
        loop = asyncio.get_running_loop()
        forever: asyncio.Future[httpx.Response] = loop.create_future()
        pending.append(forever)
        return await forever

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    sdk = AltsCodexBackend(**SDK_OPTIONS, http_client=client)

    try:
        with pytest.raises(AuthorizeCallbackTimeoutError):
            await sdk.get_slot_info("JWT_TOKEN", timeout=0.1)
    finally:
        # Release any held-open authorize requests so shutdown doesn't hang
        for fut in pending:
            if not fut.done():
                fut.set_exception(asyncio.CancelledError())
        await sdk.shutdown()


@pytest.mark.asyncio
async def test_scenario_4_concurrent_logins_do_not_interfere() -> None:
    """Two simultaneous get_slot_info calls each resolve via their own state."""

    captured_states: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/authorize" in request.url.path:
            captured_states.append(request.url.params["state"])
            return json_response({"success": True})
        if "/get_token" in request.url.path:
            return json_response(
                {
                    "id": "slot-1",
                    "access_token": "token",
                    "content_address": "addr",
                    "token_nickname": "nick",
                    "tr_cnt": 0,
                    "code": "code",
                }
            )
        return json_response({}, status_code=404)

    sdk = make_sdk(handler)
    try:
        t1 = asyncio.create_task(sdk.get_slot_info("JWT_1"))
        t2 = asyncio.create_task(sdk.get_slot_info("JWT_2"))

        await asyncio.sleep(0.05)

        assert len(captured_states) == 2
        assert captured_states[0] != captured_states[1]

        await sdk.handle_callback(
            fake_request({"success": "1", "code": "code1", "state": captured_states[0]})
        )
        await sdk.handle_callback(
            fake_request({"success": "1", "code": "code2", "state": captured_states[1]})
        )

        result1, result2 = await asyncio.gather(t1, t2)
        assert result1["access_token"] == "token"
        assert result2["access_token"] == "token"
    finally:
        await sdk.shutdown()


@pytest.mark.asyncio
async def test_scenario_5_no_memory_leak_after_timeouts() -> None:
    """After three timeouts, the pending map drains to empty."""

    async def handler(request: httpx.Request) -> httpx.Response:
        # Hold authorize open forever — let the timeout do the cleanup
        loop = asyncio.get_running_loop()
        forever: asyncio.Future[httpx.Response] = loop.create_future()
        return await forever

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    sdk = AltsCodexBackend(**SDK_OPTIONS, http_client=client)

    async def run() -> None:
        try:
            await sdk.get_slot_info("JWT", timeout=0.05)
        except Exception:
            pass

    try:
        await asyncio.gather(run(), run(), run())
        # Give the cleanup tasks scheduled by the timeout a tick to run
        await asyncio.sleep(0.05)
        assert len(sdk._pending_by_state) == 0
    finally:
        await sdk.shutdown()


@pytest.mark.asyncio
async def test_scenario_6_callback_success_zero_rejects() -> None:
    """Callback with success != "1" rejects the pending request."""

    captured_state: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/authorize" in request.url.path:
            captured_state.append(request.url.params["state"])
            return json_response({"success": True})
        return json_response({}, status_code=404)

    sdk = make_sdk(handler)
    try:
        task = asyncio.create_task(sdk.get_slot_info("JWT_TOKEN"))
        await asyncio.sleep(0.05)
        assert captured_state

        await sdk.handle_callback(
            fake_request({"success": "0", "state": captured_state[0]})
        )

        with pytest.raises(AuthorizeRejectedError):
            await task
    finally:
        await sdk.shutdown()


# ---------------------------------------------------------------------------
# Constructor / contract checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_constructor_validates_required_options() -> None:
    with pytest.raises(ValueError, match="client_id"):
        AltsCodexBackend(client_id="", client_secret="x", redirect_uri="x")
    with pytest.raises(ValueError, match="client_secret"):
        AltsCodexBackend(client_id="x", client_secret="", redirect_uri="x")
    with pytest.raises(ValueError, match="redirect_uri"):
        AltsCodexBackend(client_id="x", client_secret="x", redirect_uri="")


@pytest.mark.asyncio
async def test_client_secret_is_not_a_public_attribute() -> None:
    sdk = AltsCodexBackend(**SDK_OPTIONS)
    try:
        # Public attributes must not expose the secret directly
        for name in dir(sdk):
            if name.startswith("_"):
                continue
            value = getattr(sdk, name, None)
            assert value != SDK_OPTIONS["client_secret"], (
                f"client_secret leaked via public attribute {name!r}"
            )
    finally:
        await sdk.shutdown()


@pytest.mark.asyncio
async def test_handle_callback_with_unknown_state_is_noop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"success": True})

    sdk = make_sdk(handler)
    try:
        ack = await sdk.handle_callback(
            fake_request({"success": "1", "code": "x", "state": "ghost"})
        )
        assert ack == {"received": True}
    finally:
        await sdk.shutdown()
