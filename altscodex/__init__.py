# AltsCodex DeOAuth SDK 의 Python 진입점 — Backend/Frontend 클래스와 타입 재노출
"""AltsCodex DeOAuth SDK for Python.

Port of the official ``@altscodex/sdk`` npm package, designed for FastAPI
and other async Python web frameworks.

Quick start (FastAPI)::

    from fastapi import FastAPI, Request
    from altscodex import AltsCodexBackend

    sdk = AltsCodexBackend(
        client_id="YOUR_CLIENT_ID",
        client_secret=os.environ["ALTSCODEX_CLIENT_SECRET"],
        redirect_uri="https://yourapp.com/getinfo",
    )

    app = FastAPI()

    @app.post("/getinfo")
    async def getinfo(request: Request):
        return await sdk.handle_callback(request)

    @app.post("/login")
    async def login(jwt: str):
        return await sdk.get_slot_info(jwt)
"""

from altscodex.backend import AltsCodexBackend
from altscodex.exceptions import (
    AltsCodexError,
    AltsCodexHTTPError,
    AuthorizeCallbackTimeoutError,
    AuthorizeFailedError,
    AuthorizeRejectedError,
    ShutdownError,
)
from altscodex.frontend import AltsCodex
from altscodex.types import SlotInfo

__all__ = [
    "AltsCodex",
    "AltsCodexBackend",
    "AltsCodexError",
    "AltsCodexHTTPError",
    "AuthorizeCallbackTimeoutError",
    "AuthorizeFailedError",
    "AuthorizeRejectedError",
    "ShutdownError",
    "SlotInfo",
]

__version__ = "2.1.0"
