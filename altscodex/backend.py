# AltsCodex DeOAuth Backend SDK — authorize→callback→get_token 체인을 비동기로 관리
"""AltsCodex Backend SDK.

Server-side counterpart of ``@altscodex/sdk/backend``. Manages the
DeOAuth authorize → callback → get_token chain, with concurrent
request support via a state-keyed pending map.

Designed to be mounted in a FastAPI/Starlette application::

    sdk = AltsCodexBackend(client_id=..., client_secret=..., redirect_uri=...)

    @app.post("/getinfo")
    async def getinfo(request: Request):
        return await sdk.handle_callback(request)

    @app.post("/login")
    async def login(jwt: str):
        return await sdk.get_slot_info(jwt)
"""

from __future__ import annotations

import asyncio
import base64
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import httpx

from altscodex.exceptions import (
    AltsCodexError,
    AltsCodexHTTPError,
    AuthorizeCallbackTimeoutError,
    AuthorizeFailedError,
    AuthorizeRejectedError,
    ShutdownError,
)
from altscodex.types import SlotInfo

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_AUTH_SERVER_URL = "https://api.altscodex.com"


@dataclass
class _PendingEntry:
    """Per-request state held while waiting for the DeOAuth callback."""

    future: "asyncio.Future[SlotInfo]"
    timeout_handle: asyncio.TimerHandle
    settled: bool = field(default=False)


class AltsCodexBackend:
    """Backend SDK for the AltsCodex DeOAuth flow.

    ``client_secret`` is kept in a private attribute and never exposed
    on the instance's public surface.

    Parameters
    ----------
    client_id:
        Client ID issued by the Developer Center.
    client_secret:
        Client secret issued by the Developer Center.
    redirect_uri:
        Callback URL registered for this client, exact match required.
    auth_server_url:
        DeOAuth server base URL. Defaults to the production server.
    http_client:
        Optional ``httpx.AsyncClient`` to reuse. When omitted, the SDK
        owns and closes its own client.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        auth_server_url: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not client_id:
            raise ValueError("[AltsCodexBackend] client_id required")
        if not client_secret:
            raise ValueError("[AltsCodexBackend] client_secret required")
        if not redirect_uri:
            raise ValueError("[AltsCodexBackend] redirect_uri required")

        self._auth_server_url = (auth_server_url or DEFAULT_AUTH_SERVER_URL).rstrip("/")
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self.__client_secret = client_secret

        self._pending_by_state: "dict[str, _PendingEntry]" = {}
        self._lock = asyncio.Lock()
        self._is_shutdown = False

        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_slot_info(
        self,
        jwt: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> SlotInfo:
        """Run the DeOAuth authorize → callback → get_token chain.

        The pending entry is registered **before** the authorize HTTP
        call is dispatched, so a callback that arrives faster than the
        authorize response is still routed correctly.
        """

        if self._is_shutdown:
            raise ShutdownError("[AltsCodexBackend] instance already shutdown")
        if not jwt:
            raise ValueError("[AltsCodexBackend] jwt required")

        loop = asyncio.get_running_loop()
        state = self._generate_state()
        future: "asyncio.Future[SlotInfo]" = loop.create_future()

        timeout_handle = loop.call_later(
            timeout,
            lambda: asyncio.create_task(
                self._reject_pending(
                    state,
                    AuthorizeCallbackTimeoutError(
                        "[AltsCodexBackend] authorize callback timeout"
                    ),
                )
            ),
        )

        async with self._lock:
            self._pending_by_state[state] = _PendingEntry(
                future=future,
                timeout_handle=timeout_handle,
            )

        # 백그라운드로 authorize 호출. 콜백이 더 빨리 도착해도 위에서 state 가
        # 이미 pending map 에 들어있으므로 안전하다.
        asyncio.create_task(self._dispatch_authorize(jwt, state))

        return await future

    async def handle_callback(self, request: Any) -> Mapping[str, Any]:
        """Process a DeOAuth callback request.

        Accepts a Starlette/FastAPI ``Request`` (anything with
        ``.query_params`` mapping) or a plain mapping with a
        ``"query"`` key (for testability with arbitrary HTTP frameworks).

        Returns a JSON-serialisable mapping that the framework will
        send back to the DeOAuth server — typically ``{"received": True}``.
        The actual slot-info resolution happens in the background so
        the callback response is not blocked on the ``get_token``
        round-trip.
        """

        query = self._extract_query(request)
        state = query.get("state")

        # 콜백은 best-effort 응답이 우선이고, 후속 비동기 정리는 따로 진행한다.
        if not state:
            return {"received": True}

        async with self._lock:
            pending = self._pending_by_state.get(state)
        if pending is None:
            return {"received": True}

        success = query.get("success")
        if success != "1":
            fail_reason = f" (code: {query.get('code')})" if query.get("code") else ""
            asyncio.create_task(
                self._reject_pending(
                    state,
                    AuthorizeRejectedError(
                        f"[AltsCodexBackend] authorize callback rejected{fail_reason}"
                    ),
                )
            )
            return {"received": True}

        code = query.get("code")
        if not code:
            asyncio.create_task(
                self._reject_pending(
                    state,
                    AltsCodexError("[AltsCodexBackend] callback code missing"),
                )
            )
            return {"received": True}

        asyncio.create_task(self._exchange_and_resolve(state, code))
        return {"received": True}

    async def shutdown(self) -> None:
        """Reject every still-pending request and release resources.

        Call this from a FastAPI ``shutdown`` event handler or any
        equivalent lifecycle hook to stop leaking timers/futures.
        """

        self._is_shutdown = True

        async with self._lock:
            states = list(self._pending_by_state.keys())

        for state in states:
            await self._reject_pending(
                state, ShutdownError("[AltsCodexBackend] shutdown")
            )

        if self._owns_client:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _dispatch_authorize(self, jwt: str, state: str) -> None:
        """POST-style fire-and-forget: trigger the DeOAuth authorize call
        and, on failure, reject the pending future immediately."""

        # 원본 JS 의 'respose_type' 오타는 서버 계약이므로 보존한다.
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "respose_type": "code",
            "state": state,
        }

        try:
            payload = await self._request_json(
                "GET",
                f"{self._auth_server_url}/v1/oauth-meta/authorize",
                params=params,
                headers={"Authorization": f"Bearer {jwt}"},
            )
        except Exception as err:  # noqa: BLE001 — surface any transport error
            await self._reject_pending(
                state, err if isinstance(err, Exception) else Exception(str(err))
            )
            return

        if not (isinstance(payload, dict) and payload.get("success") is True):
            code = payload.get("code") if isinstance(payload, dict) else None
            msg = (
                payload.get("msg")
                if isinstance(payload, dict) and payload.get("msg")
                else "authorize failed"
            )
            suffix = f" ({code})" if code else ""
            await self._reject_pending(
                state, AuthorizeFailedError(f"[AltsCodexBackend] {msg}{suffix}", code=code)
            )

    async def _exchange_and_resolve(self, state: str, code: str) -> None:
        """Exchange the callback code for slot info, then resolve."""

        try:
            slot_info = await self._exchange_code(code)
        except Exception as err:  # noqa: BLE001
            await self._reject_pending(
                state, err if isinstance(err, Exception) else Exception(str(err))
            )
            return

        await self._resolve_pending(state, slot_info)

    async def _exchange_code(
        self,
        code: str,
        client_secret: Optional[str] = None,
    ) -> SlotInfo:
        """Exchange the OAuth callback code for the slot information."""

        if not code:
            raise ValueError("[AltsCodexBackend] code required")

        secret = client_secret or self.__client_secret
        token = base64.b64encode(f"{self._client_id}:{secret}".encode("utf-8")).decode(
            "ascii"
        )

        payload = await self._request_json(
            "POST",
            f"{self._auth_server_url}/v1/oauth-meta/get_token",
            json={
                "grant_type": "code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
        )

        source: Mapping[str, Any]
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("data"), list)
            and payload["data"]
        ):
            source = payload["data"][0]
        elif isinstance(payload, dict):
            source = payload
        else:
            source = {}

        return SlotInfo(
            id=source.get("id"),
            access_token=source.get("access_token"),
            content_address=source.get("content_address"),
            token_nickname=source.get("token_nickname"),
            tr_cnt=source.get("tr_cnt"),
            code=source.get("code"),
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """Lightweight wrapper around ``httpx`` that mimics the JS SDK's
        ``requestJson`` semantics: parse JSON when possible, otherwise
        return ``{"raw": text}``; raise on non-2xx with the same payload."""

        response = await self._http.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
        )

        text = response.text
        payload: Any = None
        if text:
            try:
                payload = response.json()
            except ValueError:
                payload = {"raw": text}

        if response.status_code < 200 or response.status_code >= 300:
            raise AltsCodexHTTPError(
                f"HTTP {response.status_code}",
                status=response.status_code,
                payload=payload,
            )
        return payload

    async def _resolve_pending(self, state: str, value: SlotInfo) -> None:
        async with self._lock:
            pending = self._pending_by_state.pop(state, None)
        if pending is None or pending.settled:
            return
        pending.settled = True
        pending.timeout_handle.cancel()
        if not pending.future.done():
            pending.future.set_result(value)

    async def _reject_pending(self, state: str, err: Exception) -> None:
        async with self._lock:
            pending = self._pending_by_state.pop(state, None)
        if pending is None or pending.settled:
            return
        pending.settled = True
        pending.timeout_handle.cancel()
        if not pending.future.done():
            pending.future.set_exception(err)

    @staticmethod
    def _generate_state() -> str:
        return secrets.token_urlsafe(24)

    @staticmethod
    def _extract_query(request: Any) -> Mapping[str, Any]:
        """Pull a flat ``{name: value}`` mapping out of a Starlette/FastAPI
        ``Request`` or a generic ``{"query": {...}}`` dict."""

        if hasattr(request, "query_params"):
            return dict(request.query_params)
        if isinstance(request, Mapping):
            inner = request.get("query")
            if isinstance(inner, Mapping):
                return dict(inner)
            return dict(request)
        return {}
