# AltsCodex SDK 에러 계층 — HTTP 응답 매핑이 쉬워지도록 의미별로 세분화
"""Exception classes for the AltsCodex SDK.

The hierarchy is shallow on purpose: every error inherits from
:class:`AltsCodexError`, and HTTP-status mappings can be derived
from the class name.
"""

from __future__ import annotations

from typing import Any, Optional


class AltsCodexError(Exception):
    """Base exception for every error raised by this SDK."""


class AltsCodexHTTPError(AltsCodexError):
    """Raised when the DeOAuth server returns a non-2xx status."""

    def __init__(
        self,
        message: str,
        status: int,
        payload: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class AuthorizeCallbackTimeoutError(AltsCodexError):
    """Raised when the callback from the DeOAuth server does not arrive
    within the configured timeout. Recommended HTTP status: 408."""


class AuthorizeFailedError(AltsCodexError):
    """Raised when the DeOAuth ``/authorize`` endpoint returns
    ``success: false``. Recommended HTTP status: 401 (for EXPIRED_TOKEN)
    or 502 (for AUTHORIZE_ERROR)."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


class AuthorizeRejectedError(AltsCodexError):
    """Raised when the callback arrives with ``success != "1"``.
    Recommended HTTP status: 502."""


class ShutdownError(AltsCodexError):
    """Raised when the SDK is shut down while a request is still pending.
    Recommended HTTP status: 503."""
