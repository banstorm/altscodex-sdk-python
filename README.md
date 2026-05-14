# altscodex-sdk (Python)

Official Python SDK for **AltsCodex DeOAuth** — a decentralized identity layer
that bridges OAuth with on-chain account abstraction. This package is the
async-Python port of [`@altscodex/sdk`](https://www.npmjs.com/package/@altscodex/sdk)
(npm).

> 📚 **Docs · Support · Sign up** — [developers.altscodex.com](https://developers.altscodex.com)
> 🏠 **Platform** — [altscodex.com](https://altscodex.com)

- **Async-native** — built on `httpx.AsyncClient` and `asyncio.Future`
- **FastAPI-friendly** — `handle_callback(request)` accepts a Starlette `Request`
- **Concurrency-safe** — state-keyed pending map, per-request timeout, graceful shutdown
- **Secret-safe** — `client_secret` is held in a name-mangled private attribute
- **Test-friendly** — accepts an injected `httpx.AsyncClient` (use `MockTransport`)
- **Zero hidden state** — the SDK reads no environment variables; you pass options explicitly

---

## Table of Contents

- [What this SDK does](#what-this-sdk-does)
- [When to use it (and when not to)](#when-to-use-it-and-when-not-to)
- [Installation](#installation)
- [Quick start](#quick-start)
- [How the full flow works](#how-the-full-flow-works)
- [API reference](#api-reference)
  - [`AltsCodexBackend`](#altscodexbackend)
  - [`AltsCodex` (frontend helper)](#altscodex-frontend-helper)
  - [`SlotInfo`](#slotinfo)
  - [Exceptions](#exceptions)
- [Integration patterns](#integration-patterns)
- [Concurrency model](#concurrency-model)
- [Configuration](#configuration)
- [Error handling & HTTP status mapping](#error-handling--http-status-mapping)
- [Testing your integration](#testing-your-integration)
- [Local development](#local-development)
- [Security](#security)
- [Common pitfalls](#common-pitfalls)
- [Comparison with the JavaScript SDK](#comparison-with-the-javascript-sdk)
- [Publishing this SDK](#publishing-this-sdk)
- [Contributing](#contributing)
- [Resources](#resources)
- [License](#license)

---

## What this SDK does

AltsCodex DeOAuth is a three-party OAuth flow extended with an on-chain
identity layer. The browser obtains a short-lived **JWT** from the AltsCodex
platform server, then your backend uses this SDK to exchange that JWT for the
user's **slot information** (account id, content address, etc.).

Two responsibilities live in this package:

1. **`AltsCodexBackend`** — runs the `authorize → callback → get_token` chain
   against the DeOAuth server (`api.altscodex.com`). One method
   (`get_slot_info`) and one callback handler (`handle_callback`) do the
   whole thing.
2. **`AltsCodex`** — server-side helper for the browser-side flow: builds the
   login URL, generates the CSRF `state`, parses the redirect callback query.
   Use this when you render the login page from Python (e.g. Jinja, Next.js
   server actions backed by FastAPI) instead of using the JavaScript SDK
   popup.

---

## When to use it (and when not to)

**Use this SDK when**
- Your backend is FastAPI, Starlette, Quart, Sanic, or any other `asyncio`-based framework.
- You run the OAuth `get_token` exchange on the server — i.e. anywhere `client_secret` is needed.
- You issue session cookies, JWTs, or DB users keyed off `SlotInfo.id`.

**Use the [JavaScript SDK](https://www.npmjs.com/package/@altscodex/sdk) instead when**
- You need the **browser popup flow** (`window.open` + `postMessage`).
  Popups are inherently browser-only and have no Python equivalent — use
  the JS SDK on the frontend and call this Python SDK from your backend.

**Don't use either** when
- You only need to display a user's public profile — there's no public
  read-only endpoint in this SDK. Issue a session token from `SlotInfo.id`
  and call your own backend.

---

## Installation

```bash
pip install altscodex-sdk
```

With FastAPI helpers (optional — only pulls FastAPI into your env):

```bash
pip install "altscodex-sdk[fastapi]"
```

For development against this repo:

```bash
git clone https://github.com/banstorm/altscodex-sdk-python.git
cd altscodex-sdk-python
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

### Compatibility

| Item | Range |
|------|-------|
| Python | 3.9 – 3.13 |
| `httpx` | ≥ 0.24 |
| FastAPI / Starlette | any version with `Request.query_params` (≥ 0.95 in `pyproject.toml`) |
| Operating systems | any platform supported by CPython + `httpx` |

---

## Quick start

A minimal FastAPI server that completes a login round-trip.

```python
# server.py — 최소 FastAPI 통합 예제
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
    client_id=os.environ["ALTSCODEX_CLIENT_ID"],
    client_secret=os.environ["ALTSCODEX_CLIENT_SECRET"],
    redirect_uri=os.environ["ALTSCODEX_REDIRECT_URI"],
    # auth_server_url defaults to https://api.altscodex.com
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
    """DeOAuth callback endpoint. Path must match `redirect_uri` exactly."""
    return await sdk.handle_callback(request)

@app.post("/login")
async def login(payload: dict):
    """Receive a JWT from the browser SDK, return the user's slot info."""
    jwt = payload.get("jwt")
    if not jwt:
        raise HTTPException(400, "jwt required")
    try:
        slot = await sdk.get_slot_info(jwt)
    except AuthorizeFailedError as err:
        status = 401 if err.code == "EXPIRED_TOKEN" else 502
        raise HTTPException(status, str(err)) from err
    except AuthorizeCallbackTimeoutError as err:
        raise HTTPException(408, str(err)) from err
    except AuthorizeRejectedError as err:
        raise HTTPException(502, str(err)) from err
    except ShutdownError as err:
        raise HTTPException(503, str(err)) from err
    return {"success": True, "user": slot}
```

Run it:

```bash
ALTSCODEX_CLIENT_ID=... \
ALTSCODEX_CLIENT_SECRET=... \
ALTSCODEX_REDIRECT_URI=https://yourapp.com/getinfo \
uvicorn server:app --port 3070
```

A more complete version of the same example lives in
[`examples/fastapi_app.py`](examples/fastapi_app.py).

---

## How the full flow works

```
[Browser]                  [Your Backend]                 [DeOAuth Server]
   |                              |                              |
   |  1. JS SDK opens popup ----> | (no backend involvement)     |
   |  2. user logs in   <-------- popup on altscodex.com         |
   |  3. JS SDK receives JWT      |                              |
   |  4. POST /login { jwt } ---> |                              |
   |                              |  5. get_slot_info(jwt)       |
   |                              |     pre-registers `state`    |
   |                              |     in pending map           |
   |                              |  6. GET /authorize ---------> |
   |                              |       (Bearer jwt + state)   |
   |                              |     <---- success: true -----|
   |                              |                              |
   |                              |  ... DeOAuth fires callback  |
   |                              |  7. POST /getinfo <----------|
   |                              |       (query: state, code,   |
   |                              |        success=1)            |
   |                              |  8. handle_callback ack 200  |
   |                              |     spawns _exchange_code    |
   |                              |  9. POST /get_token --------> |
   |                              |       (Basic id:secret)      |
   |                              |     <---- slot info ---------|
   |                              | 10. resolve pending future   |
   |  11. {user: slotInfo} <----- |                              |
```

Key invariants:

- **Step 5 happens before step 6** — the pending entry is created
  *before* the authorize HTTP request is dispatched, so a callback that
  arrives faster than the authorize response (it can happen — the DeOAuth
  server pipes them concurrently) is still routed correctly.
- **Steps 6 and 7 use different transports** — step 6 is your backend's
  HTTP client; step 7 is the DeOAuth server calling back into your backend.
- **Step 8 is synchronous, step 9 is fire-and-forget** — the DeOAuth
  server gets its 200 OK immediately so its connection doesn't block on
  step 9.

---

## API reference

### `AltsCodexBackend`

```python
class AltsCodexBackend:
    def __init__(
        self,
        *,
        client_id: str,                                # required
        client_secret: str,                            # required — held privately
        redirect_uri: str,                             # required, exact match
        auth_server_url: str | None = None,            # default: https://api.altscodex.com
        http_client: httpx.AsyncClient | None = None,  # optional, for reuse / testing
    ) -> None: ...
```

`client_secret` is stored in a name-mangled attribute
(`_AltsCodexBackend__client_secret`) and is **not** exposed on the instance's
public surface. Tests in this repo enforce that no public attribute equals the
configured secret.

If `http_client` is omitted, the SDK constructs its own `httpx.AsyncClient(timeout=30.0)`
and closes it on `shutdown()`. If you pass one, you keep ownership.

#### `await sdk.get_slot_info(jwt: str, *, timeout: float = 15.0) -> SlotInfo`

Runs the full chain. The current task awaits a future that is resolved when
either:

- the DeOAuth server posts a successful callback and `_exchange_code` returns, or
- the authorize call fails immediately (returns `success: false`), or
- the `timeout` elapses without a callback.

```python
slot = await sdk.get_slot_info(jwt, timeout=20.0)
print(slot["id"], slot["content_address"])
```

Raises one of [the exceptions below](#exceptions).

#### `await sdk.handle_callback(request) -> dict`

Receives the DeOAuth callback. Returns `{"received": True}` immediately;
the `get_token` exchange runs as a background `asyncio.Task` and resolves
the matching pending future.

```python
@app.post("/getinfo")
async def callback(request: Request):
    return await sdk.handle_callback(request)
```

`request` may be:

- a Starlette/FastAPI `Request` (anything exposing `.query_params`), or
- a plain mapping shaped like `{"query": {...}}` (useful for tests or
  non-FastAPI frameworks that wrap the query string differently).

The DeOAuth server sends callbacks via **POST**. If you accidentally
register it as `GET`, FastAPI's automatic 405 will fire and your pending
request will time out.

#### `await sdk.shutdown()`

- Marks the SDK as shut down — further `get_slot_info` calls raise `ShutdownError`.
- Cancels every pending future's timeout handle and rejects each one with `ShutdownError`.
- Closes the owned `httpx.AsyncClient` (only if the SDK constructed it).

Always call this from your FastAPI `lifespan` or `shutdown` hook. Without
it, in-flight requests leak futures and timers when your worker exits.

---

### `AltsCodex` (frontend helper)

```python
class AltsCodex:
    def __init__(
        self,
        *,
        client_id: str,                       # required
        redirect_uri: str,                    # required
        altscodex_url: str | None = None,     # default: https://altscodex.com
        response_type: str = "code",
    ) -> None: ...

    def build_login_url(self, *, state: str | None = None) -> LoginUrl: ...
    @staticmethod
    def parse_callback(query: Mapping[str, str]) -> CallbackPayload: ...
    @staticmethod
    def generate_state() -> str: ...
```

The browser popup flow (`window.open`, `postMessage`, `localStorage`) has
no direct Python analogue. This helper handles the two pieces that *do*
translate:

#### `helper.build_login_url(*, state=None) -> LoginUrl`

Build the URL the browser should be redirected to. `LoginUrl` has `.url`
and `.state` attributes and is iterable, so unpacking works:

```python
url, state = helper.build_login_url()
request.session["altscodex_state"] = state    # store for CSRF check
return RedirectResponse(url)
```

#### `AltsCodex.parse_callback(query) -> CallbackPayload`

Parse a server-side redirect callback. `CallbackPayload` exposes
`success: bool`, `code: str | None`, `state: str | None`, and `raw: dict`.

```python
payload = AltsCodex.parse_callback(dict(request.query_params))
if payload.state != request.session.get("altscodex_state"):
    raise HTTPException(403, "state mismatch")
if not payload.success:
    raise HTTPException(401, "login failed")
# Use payload.code to exchange for slot info via AltsCodexBackend._exchange_code
```

#### `AltsCodex.generate_state() -> str`

Returns a `secrets.token_urlsafe(24)` string (~192 bits of entropy).
Suitable for CSRF protection.

---

### `SlotInfo`

```python
class SlotInfo(TypedDict, total=False):
    id:              str | None     # stable user identifier — use as your primary key
    access_token:    str | None     # DeOAuth access token (NOT your session token)
    content_address: str | None     # on-chain wallet address
    token_nickname:  str | None     # slot nickname chosen by the user
    tr_cnt:          int | None     # transfer count (on-chain activity counter)
    code:            str | None     # the OAuth code that was just exchanged
```

All fields are typed as `Optional` because the upstream server can omit any
of them. `id` and `access_token` are present in normal cases. Treat
anything you depend on as nullable until you've verified it server-side.

---

### Exceptions

```
AltsCodexError                        # base — catch this if you want everything
├── AltsCodexHTTPError                # non-2xx from DeOAuth (.status, .payload)
├── AuthorizeCallbackTimeoutError     # callback did not arrive in time
├── AuthorizeFailedError              # /authorize returned success=false (.code)
├── AuthorizeRejectedError            # callback arrived with success != "1"
└── ShutdownError                     # SDK shut down with this request pending
```

`AuthorizeFailedError.code` carries the server-provided code string (e.g.
`"EXPIRED_TOKEN"`, `"AUTHORIZE_ERROR"`) so you can distinguish 401-class
from 502-class failures.

`AltsCodexHTTPError.status` is the HTTP status code returned by the DeOAuth
server, and `.payload` is the parsed JSON body (or `{"raw": text}` if the
body was not JSON).

Recommended HTTP-status mapping for your own endpoints:

| Exception | HTTP |
|-----------|------|
| `AuthorizeFailedError` (`code == "EXPIRED_TOKEN"`) | **401** |
| `AuthorizeFailedError` (other codes) | **502** |
| `AuthorizeCallbackTimeoutError` | **408** |
| `AuthorizeRejectedError` | **502** |
| `ShutdownError` | **503** |
| `AltsCodexHTTPError` | mirror `.status` |
| `AltsCodexError` (catch-all) | **500** |

---

## Integration patterns

### Lifespan-managed singleton (recommended)

One `AltsCodexBackend` per process. Construct at startup, shut down at exit:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sdk = AltsCodexBackend(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        redirect_uri=settings.redirect_uri,
    )
    try:
        yield
    finally:
        await app.state.sdk.shutdown()

app = FastAPI(lifespan=lifespan)

def get_sdk(request: Request) -> AltsCodexBackend:
    return request.app.state.sdk

@app.post("/login")
async def login(payload: dict, sdk: AltsCodexBackend = Depends(get_sdk)):
    return await sdk.get_slot_info(payload["jwt"])
```

### Server-side redirect flow (no popup)

If you can't use the JS popup (e.g. native mobile webview), do a plain
redirect flow:

```python
helper = AltsCodex(
    client_id=settings.client_id,
    redirect_uri="https://yourapp.com/auth/callback",
)

@app.get("/auth/start")
async def start(request: Request):
    login = helper.build_login_url()
    request.session["altscodex_state"] = login.state
    return RedirectResponse(login.url)

@app.get("/auth/callback")
async def callback(request: Request, sdk: AltsCodexBackend = Depends(get_sdk)):
    payload = AltsCodex.parse_callback(dict(request.query_params))
    if payload.state != request.session.pop("altscodex_state", None):
        raise HTTPException(403, "state mismatch")
    if not payload.success or not payload.code:
        raise HTTPException(401, "login failed")
    # Note: this path is the redirect-flow shortcut. The standard JWT
    # path through sdk.get_slot_info / sdk.handle_callback still works
    # in parallel — pick one model per route.
```

> The DeOAuth server's default callback transport is **POST** (the JS popup
> case). The pure redirect flow above uses **GET** and is supported only
> if your client is configured for it — confirm with the Developer Center
> before relying on this pattern in production.

### Multi-tenant / multi-client

If your app serves multiple AltsCodex client applications, instantiate
**one SDK per client** and key the instances by `client_id`:

```python
class SdkRegistry:
    def __init__(self) -> None:
        self._by_client: dict[str, AltsCodexBackend] = {}

    def get(self, client_id: str) -> AltsCodexBackend:
        if client_id not in self._by_client:
            cfg = load_client_config(client_id)  # from your DB
            self._by_client[client_id] = AltsCodexBackend(
                client_id=client_id,
                client_secret=cfg.secret,
                redirect_uri=cfg.redirect_uri,
            )
        return self._by_client[client_id]

    async def shutdown(self) -> None:
        for sdk in self._by_client.values():
            await sdk.shutdown()
```

Each `client_secret` stays scoped to its own SDK instance and never crosses
tenant boundaries.

---

## Concurrency model

- The **pending map** is `dict[str, _PendingEntry]` guarded by an
  `asyncio.Lock`. All add / remove operations are awaited inside the lock.
- Each pending entry holds an `asyncio.Future` and a `loop.call_later`
  timeout handle. The timeout fires `_reject_pending(state, …)` if the
  callback never arrives.
- **State generation** uses `secrets.token_urlsafe(24)` — collision-resistant
  for any realistic concurrency level.
- **Authorize dispatch is fire-and-forget** — the `get_slot_info` coroutine
  registers the pending entry, schedules `_dispatch_authorize` with
  `asyncio.create_task`, and awaits the future. This is what lets a fast
  callback resolve the future *before* the authorize response returns.
- **`handle_callback` returns synchronously**; the `get_token` exchange
  runs in a separate task. If your event loop is shutting down while this
  task is in flight, `shutdown()` will reject the corresponding pending
  future with `ShutdownError`.
- The SDK is **safe under multiple concurrent `get_slot_info` calls** with
  the same JWT or different JWTs. Each call gets its own `state` and its
  own pending entry. The state-keyed map prevents any cross-talk.
- The SDK is **not safe across processes** — pending state is in-memory.
  If you run multiple uvicorn workers and the callback arrives at a
  different worker than the one that initiated `authorize`, the callback
  silently no-ops and the originating request will time out. Pin to one
  worker, use sticky sessions, or move pending state to Redis (see
  [Extending](#extending) below).

### Extending

The SDK is intentionally small; for advanced needs subclass or compose:

- **Cross-worker pending state** — override `_resolve_pending` /
  `_reject_pending` / the `_pending_by_state` storage to back it with
  Redis pub/sub. State is a 32-byte URL-safe string; treat it as the
  Redis key.
- **Custom retry / proxy / TLS** — pass `http_client=httpx.AsyncClient(...)`.
  Anything `httpx` supports works (proxies, custom CAs, HTTP/2, etc.).
- **Custom telemetry** — wrap `get_slot_info` in a decorator that records
  timing and exception classes. The SDK doesn't ship its own metrics by
  design.

---

## Configuration

The SDK reads **no environment variables**. All configuration is passed
explicitly to the constructor. This keeps secrets out of `os.environ`
leaks and makes per-request configuration possible (multi-tenant).

A typical environment-variable layout for production deployments:

```bash
# .env (server side only — never expose to the browser)
ALTSCODEX_AUTH_SERVER_URL=https://api.altscodex.com
ALTSCODEX_CLIENT_ID=your-registered-client-id
ALTSCODEX_CLIENT_SECRET=your-client-secret
ALTSCODEX_REDIRECT_URI=https://yourapp.com/getinfo
```

```python
sdk = AltsCodexBackend(
    auth_server_url=os.environ.get("ALTSCODEX_AUTH_SERVER_URL"),
    client_id=os.environ["ALTSCODEX_CLIENT_ID"],
    client_secret=os.environ["ALTSCODEX_CLIENT_SECRET"],
    redirect_uri=os.environ["ALTSCODEX_REDIRECT_URI"],
)
```

For Pydantic settings users:

```python
from pydantic_settings import BaseSettings

class AltsCodexSettings(BaseSettings):
    auth_server_url: str = "https://api.altscodex.com"
    client_id: str
    client_secret: str
    redirect_uri: str

    model_config = {"env_prefix": "ALTSCODEX_"}

settings = AltsCodexSettings()
sdk = AltsCodexBackend(**settings.model_dump())
```

### Tuning

| Knob | Default | When to change |
|------|---------|----------------|
| `get_slot_info(..., timeout=...)` | `15.0` seconds | Increase for slow networks; decrease if you want faster failure |
| `httpx.AsyncClient(timeout=...)` | `30.0` seconds | Pass `http_client=...` with a custom timeout if needed |
| `auth_server_url` | `https://api.altscodex.com` | Override for staging / local DeOAuth server |

---

## Error handling & HTTP status mapping

A complete error handler:

```python
from altscodex import (
    AltsCodexError,
    AltsCodexHTTPError,
    AuthorizeCallbackTimeoutError,
    AuthorizeFailedError,
    AuthorizeRejectedError,
    ShutdownError,
)

@app.exception_handler(AltsCodexError)
async def altscodex_exception_handler(_request, exc: AltsCodexError):
    if isinstance(exc, AuthorizeFailedError):
        status = 401 if exc.code == "EXPIRED_TOKEN" else 502
    elif isinstance(exc, AuthorizeCallbackTimeoutError):
        status = 408
    elif isinstance(exc, AuthorizeRejectedError):
        status = 502
    elif isinstance(exc, ShutdownError):
        status = 503
    elif isinstance(exc, AltsCodexHTTPError):
        status = exc.status
    else:
        status = 500
    return JSONResponse({"error": str(exc)}, status_code=status)
```

Register this once and your route handlers can call `sdk.get_slot_info`
without per-route try/except.

---

## Testing your integration

The SDK is designed to be testable without a real DeOAuth server. Inject
an `httpx.AsyncClient` backed by `MockTransport`:

```python
import json

import httpx
import pytest

from altscodex import AltsCodexBackend


@pytest.fixture
async def sdk():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/authorize" in request.url.path:
            return httpx.Response(
                200,
                content=json.dumps({"success": True}).encode(),
                headers={"content-type": "application/json"},
            )
        if "/get_token" in request.url.path:
            return httpx.Response(
                200,
                content=json.dumps({"id": "u-1", "access_token": "t"}).encode(),
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    instance = AltsCodexBackend(
        client_id="cid",
        client_secret="cs",
        redirect_uri="http://localhost/cb",
        http_client=client,
    )
    yield instance
    await instance.shutdown()


@pytest.mark.asyncio
async def test_login_succeeds(sdk):
    import asyncio
    task = asyncio.create_task(sdk.get_slot_info("jwt"))
    await asyncio.sleep(0.05)

    state = next(iter(sdk._pending_by_state))   # peek the state for the test
    await sdk.handle_callback({"query": {"success": "1", "code": "c", "state": state}})

    result = await task
    assert result["id"] == "u-1"
```

The repository's own [`tests/test_backend.py`](tests/test_backend.py) is a
full reference — it ports all six Jest scenarios from the JavaScript SDK
plus contract tests for the secret-leak guarantee and the unknown-state
no-op.

### End-to-end testing

For real end-to-end tests, point `auth_server_url` at a local DeOAuth
server (the AltsCodex platform exposes a docker-compose target — see the
Developer Center). Don't run E2E tests against the production DeOAuth
server; you'll burn tokens.

---

## Local development

Two things to override:

```python
sdk = AltsCodexBackend(
    auth_server_url="http://localhost:3000",
    client_id="your-local-client-id",
    client_secret="your-local-client-secret",
    redirect_uri="http://localhost:3070/getinfo",
)
```

Local DeOAuth server requirements:

1. `client_id` / `client_secret` registered in the local Developer Center.
2. `redirect_uri` registered exactly — including protocol, port, and trailing
   slash (or absence thereof).
3. CORS allowed from your frontend dev origin if you're using the JS SDK
   popup.

Running this repo's tests:

```bash
pip install -e ".[dev]"
pytest -v
```

---

## Security

- **Never put `client_secret` in frontend code.** It's only valid in
  server-side environments where `AltsCodexBackend` lives.
- The SDK stores `client_secret` in `_AltsCodexBackend__client_secret`
  (name-mangled) so it doesn't appear on `dir(instance)` or get
  accidentally serialised by frameworks that pickle/dump public
  attributes. A unit test in this repo enforces that no public attribute
  matches the configured secret.
- **`redirect_uri` must match the Developer Center registration exactly.**
  Including protocol, host, port, path, and trailing slash. Mismatches
  surface as `invalid_client` / `redirect_uri mismatch` 401 errors.
- **CSRF `state` is mandatory** for the redirect flow. The popup flow
  delivers the JWT via `postMessage` to the same origin and doesn't need
  CSRF, but the redirect flow does — store `state` in the user session
  and compare on callback.
- **Validate `SlotInfo.id` before you trust it.** Use it as a stable user
  identifier in your DB, but don't echo it back unhashed in URLs if your
  threat model includes enumeration.
- **Don't log JWTs or `access_token` values.** They grant DeOAuth-level
  access for their lifetime.

---

## Common pitfalls

### 1. Wrong subdomain

| Purpose | Production | Local |
|---------|------------|-------|
| Frontend (platform) | `https://altscodex.com` (or `www.`) | `http://localhost:3000` |
| Backend / API | `https://api.altscodex.com` | `http://localhost:3000` |
| Developer Center | `https://developers.altscodex.com` | — |

Do **NOT** invent subdomains like `login.altscodex.com`, `oauth.altscodex.com`,
`auth.altscodex.com`. They resolve to NXDOMAIN and the request silently
fails with `User closed the login window` / `authorize callback timeout`.

### 2. Forgetting to register `redirect_uri`

The first deploy to staging or production is the most common time to hit
this. Register every variant of `redirect_uri` you actually use (HTTP vs
HTTPS, with vs without trailing slash, every preview-URL host).

### 3. POST vs GET on the callback route

The DeOAuth server posts to the registered callback. If you wire it as
`@app.get` instead of `@app.post`, FastAPI returns 405 and your pending
request times out. There is no separate error code — diagnose by checking
your access log for a 405 on the callback path.

### 4. Forgetting `await sdk.shutdown()`

Without it, pending futures and timers leak when your worker exits, and
the `httpx.AsyncClient` stays open. In dev this manifests as
`ResourceWarning: unclosed client` on shutdown. In production it manifests
as a slow exit that times out the worker reaper.

### 5. Calling `get_slot_info` outside of a running event loop

This SDK is fully async. Calling `get_slot_info(...)` without awaiting it
returns a coroutine object, not a result. If you call it from sync code,
wrap with `asyncio.run(...)` or use `anyio.from_thread.run`.

### 6. Multi-worker uvicorn

The pending map is per-process. If uvicorn spawns workers `--workers 4`
and the callback hits a different worker than the one that called
`authorize`, the request times out. For now, run a single worker or pin
sessions; future versions may support Redis-backed state.

### 7. Migrating from `@webxcom/sdk`

If you're migrating an existing `@webxcom/sdk` integration, the v2.x
platform supports both client SDKs simultaneously (dual-broadcast
`postMessage`). The Python SDK is v2.x only — there is no `@webxcom`
Python equivalent. Migrate the backend first, then the frontend.

---

## Comparison with the JavaScript SDK

| Feature | `@altscodex/sdk` (Node) | `altscodex-sdk` (Python) |
|---------|-------------------------|--------------------------|
| Browser popup login | ✅ `new AltsCodex().login()` | ❌ (use JS SDK; Python serves the JWT-receiving backend) |
| `localStorage` token storage | ✅ | ❌ (Python is server-side; use cookies/JWT) |
| Build login URL (server side) | ❌ (URL is built inside `login()`) | ✅ `AltsCodex().build_login_url()` |
| Parse callback query | ❌ (handled inside backend SDK) | ✅ `AltsCodex.parse_callback()` |
| OAuth `authorize → get_token` chain | ✅ `AltsCodexBackend.getSlotInfo()` | ✅ `AltsCodexBackend.get_slot_info()` |
| Express integration | ✅ `app.post('/getinfo', sdk.handleCallback)` | ✅ `await sdk.handle_callback(request)` |
| Concurrency-safe pending map | ✅ | ✅ |
| Graceful shutdown | ✅ `sdk.shutdown()` | ✅ `await sdk.shutdown()` |
| `client_secret` privacy | closure | name-mangled attribute (enforced by tests) |
| HTTP client | built-in `fetch` / `http` | `httpx.AsyncClient` (injectable) |
| Test mocks | jest `global.fetch` | `httpx.MockTransport` |
| Test scenarios | 6 | 6 (1:1 port) + 8 contract checks |

The two SDKs are designed to interoperate. Typical deployment: JS SDK on
the frontend, Python SDK on the backend.

---

## Publishing this SDK

This repository is configured for **PyPI Trusted Publisher (OIDC)** —
you don't need a long-lived API token on PyPI for the standard release
path.

### Release a new version (OIDC path)

1. Bump `version = "..."` in `pyproject.toml`.
2. Update `__version__` in `altscodex/__init__.py` to match.
3. Commit: `git commit -am "chore: release vX.Y.Z"`.
4. Tag and push: `git tag vX.Y.Z && git push origin main vX.Y.Z`.
5. GitHub Actions builds, runs `twine check`, then uploads to PyPI via OIDC.

The workflow file is [`.github/workflows/publish.yml`](.github/workflows/publish.yml).
The PyPI Trusted Publisher must be configured (one-time setup) with:

| PyPI form field | Value |
|-----------------|-------|
| PyPI project name | `altscodex-sdk` |
| Owner | `banstorm` |
| Repository | `altscodex-sdk-python` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The GitHub repo also needs an environment named `pypi` (Settings →
Environments → New environment).

### Manual / fallback (token path)

If OIDC is unavailable (CI down, hotfix from a laptop), use
[`scripts/publish.sh`](scripts/publish.sh):

```bash
# Build + twine check only (no upload)
scripts/publish.sh --check

# Upload to TestPyPI first (recommended for first release)
scripts/publish.sh --test
pip install --index-url https://test.pypi.org/simple/ altscodex-sdk==X.Y.Z

# Production upload
scripts/publish.sh
```

Credentials: set `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=pypi-...`
in your environment, or put them in `~/.pypirc`.

---

## Contributing

1. Fork and clone the repo.
2. `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.
3. Make your change. Add a test for it. Keep tests deterministic — never
   depend on wall-clock time without `pytest`'s `tmp_path` / mocked clocks.
4. `pytest -v` — all tests must pass on Python 3.9 / 3.10 / 3.11 / 3.12 /
   3.13.
5. Match existing style: module-level Korean header comments, type
   annotations on every public function, no `Any` in public signatures
   unless it's intentional.
6. PR title in conventional commits (`feat:`, `fix:`, `refactor:`, etc.).

### Project layout

```
altscodex-sdk-python/
├── altscodex/                      # the package itself
│   ├── __init__.py                 # public API re-exports + version
│   ├── backend.py                  # AltsCodexBackend
│   ├── frontend.py                 # AltsCodex (URL builder + callback parser)
│   ├── exceptions.py               # AltsCodexError hierarchy
│   └── types.py                    # SlotInfo TypedDict
├── examples/
│   └── fastapi_app.py              # runnable FastAPI integration
├── scripts/
│   └── publish.sh                  # token-based publishing fallback
├── tests/
│   ├── test_backend.py             # 6 Jest scenarios + contract tests
│   └── test_frontend.py            # URL builder + callback parser tests
├── .github/workflows/
│   └── publish.yml                 # OIDC trusted-publisher release
├── pyproject.toml
└── README.md                       # this file
```

### Running tests

```bash
pytest -v                                  # all tests
pytest tests/test_backend.py -v            # backend only
pytest -k "scenario_4" -v                  # one scenario
pytest --co                                # collect, don't run
```

---

## Resources

| Resource | URL | Description |
|----------|-----|-------------|
| Platform | [altscodex.com](https://altscodex.com) | Sign up, manage Alts, marketplace |
| Developer Center | [developers.altscodex.com](https://developers.altscodex.com) | API docs, credentials, support |
| DeOAuth API | `https://api.altscodex.com` | Backend SDK target |
| Blockchain Explorer | [scan.xotown.com](https://scan.xotown.com) | On-chain identity lookup |
| JS SDK | [@altscodex/sdk](https://www.npmjs.com/package/@altscodex/sdk) | Browser-side counterpart |
| This repo | [github.com/banstorm/altscodex-sdk-python](https://github.com/banstorm/altscodex-sdk-python) | Source + issues |

---

## License

MIT — see [LICENSE](LICENSE) if present, otherwise the MIT license terms
in `pyproject.toml` apply.
