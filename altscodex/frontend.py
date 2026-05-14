# 서버 측 렌더링/리다이렉트용 AltsCodex 로그인 URL 빌더 (브라우저 SDK 의 보조 도구)
"""Server-side helper for the AltsCodex frontend flow.

The real frontend SDK is browser-only (popup + ``localStorage`` +
``postMessage``), which has no direct analogue in Python. This module
provides the pieces that *do* translate: building the OAuth login URL,
generating a CSRF state value, and parsing the standard redirect
callback parameters.

Use it when you serve the login page from a Python backend
(e.g. Jinja templates) and want to redirect the browser to the
AltsCodex OAuth server, or when you handle a server-side redirect
callback (instead of the browser popup flow).
"""

from __future__ import annotations

import secrets
from typing import Mapping, Optional
from urllib.parse import urlencode

DEFAULT_ALTSCODEX_URL = "https://altscodex.com"


class AltsCodex:
    """URL builder + state helper for the AltsCodex login flow.

    Parameters
    ----------
    client_id:
        OAuth client_id issued by the Developer Center.
    redirect_uri:
        OAuth redirect_uri registered for this client.
    altscodex_url:
        AltsCodex platform server base URL. Defaults to production.
    response_type:
        OAuth response_type. Defaults to ``"code"``.
    """

    def __init__(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        altscodex_url: Optional[str] = None,
        response_type: str = "code",
    ) -> None:
        if not client_id:
            raise ValueError("[AltsCodex] client_id required")
        if not redirect_uri:
            raise ValueError("[AltsCodex] redirect_uri required")

        self._altscodex_url = (altscodex_url or DEFAULT_ALTSCODEX_URL).rstrip("/")
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._response_type = response_type

    @property
    def altscodex_url(self) -> str:
        return self._altscodex_url

    def build_login_url(self, *, state: Optional[str] = None) -> "LoginUrl":
        """Return the URL that the browser should be redirected to."""

        chosen_state = state or self.generate_state()
        params = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": self._response_type,
                "state": chosen_state,
            }
        )
        return LoginUrl(url=f"{self._altscodex_url}/oauth/login?{params}", state=chosen_state)

    @staticmethod
    def parse_callback(query: Mapping[str, str]) -> "CallbackPayload":
        """Parse the query string of a server-side redirect callback.

        Returns a :class:`CallbackPayload` whose ``success`` field is
        ``True`` only when the DeOAuth server flagged a successful login.
        """

        success_raw = str(query.get("success", "")).strip()
        return CallbackPayload(
            success=success_raw == "1",
            code=query.get("code"),
            state=query.get("state"),
            raw=dict(query),
        )

    @staticmethod
    def generate_state() -> str:
        return secrets.token_urlsafe(24)


class LoginUrl:
    """Pair of (``url``, ``state``) returned by ``build_login_url``."""

    __slots__ = ("url", "state")

    def __init__(self, *, url: str, state: str) -> None:
        self.url = url
        self.state = state

    def __iter__(self):
        yield self.url
        yield self.state


class CallbackPayload:
    """Parsed view of a DeOAuth redirect callback."""

    __slots__ = ("success", "code", "state", "raw")

    def __init__(
        self,
        *,
        success: bool,
        code: Optional[str],
        state: Optional[str],
        raw: Mapping[str, str],
    ) -> None:
        self.success = success
        self.code = code
        self.state = state
        self.raw = dict(raw)
