# FastAPI 통합 예제 — AltsCodex DeOAuth Backend SDK 사용법
"""Minimal FastAPI integration example.

Run with::

    uvicorn examples.fastapi_app:app --port 3070
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from altscodex import (
    AltsCodexBackend,
    AuthorizeCallbackTimeoutError,
    AuthorizeFailedError,
    AuthorizeRejectedError,
    ShutdownError,
)


sdk = AltsCodexBackend(
    auth_server_url=os.environ.get("ALTSCODEX_AUTH_SERVER_URL"),
    client_id=os.environ["ALTSCODEX_CLIENT_ID"],
    client_secret=os.environ["ALTSCODEX_CLIENT_SECRET"],
    redirect_uri=os.environ["ALTSCODEX_REDIRECT_URI"],
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await sdk.shutdown()


app = FastAPI(lifespan=lifespan)


@app.post("/getinfo")
async def getinfo(request: Request):
    """DeOAuth callback endpoint. Must match redirect_uri exactly."""
    return await sdk.handle_callback(request)


@app.post("/login")
async def login(payload: dict):
    """Receive a JWT from the frontend and resolve the slot info."""
    jwt = payload.get("jwt")
    if not jwt:
        raise HTTPException(status_code=400, detail="jwt required")

    try:
        slot = await sdk.get_slot_info(jwt)
    except AuthorizeCallbackTimeoutError as err:
        raise HTTPException(status_code=408, detail=str(err)) from err
    except AuthorizeFailedError as err:
        status = 401 if err.code == "EXPIRED_TOKEN" else 502
        raise HTTPException(status_code=status, detail=str(err)) from err
    except AuthorizeRejectedError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    except ShutdownError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err

    return {"success": True, "user": slot}
