# altscodex-sdk (Python)

Python port of [`@altscodex/sdk`](../sdk) — the official SDK for integrating **AltsCodex DeOAuth** into your application.

> 📚 **Docs · Support · Sign up** — [developers.altscodex.com](https://developers.altscodex.com)

This package targets **FastAPI** (and any other async Python web framework). It mirrors the JavaScript SDK's public surface and behaviour, including:

- State-keyed pending map so concurrent logins don't interfere
- Pre-registration of `state` **before** `authorize` is dispatched, so a fast callback is never dropped
- `client_secret` held in a private, name-mangled attribute — never exposed on public attributes
- Graceful `shutdown()` that rejects every pending request and releases the HTTP client

---

## Installation

```bash
pip install altscodex-sdk
# or, with FastAPI extras:
pip install "altscodex-sdk[fastapi]"
```

## Base URLs

| SDK | Default URL | Description |
|-----|-------------|-------------|
| Frontend helper | `https://altscodex.com` | AltsCodex platform server |
| Backend | `https://api.altscodex.com` | DeOAuth server (A-Server) |

Both defaults point to production. Override them for local development or staging.

---

## Quick Start — FastAPI Backend

```python
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException

from altscodex import AltsCodexBackend, AuthorizeFailedError

sdk = AltsCodexBackend(
    client_id=os.environ["ALTSCODEX_CLIENT_ID"],
    client_secret=os.environ["ALTSCODEX_CLIENT_SECRET"],
    redirect_uri=os.environ["ALTSCODEX_REDIRECT_URI"],
    # auth_server_url defaults to https://api.altscodex.com
)

@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        await sdk.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/getinfo")
async def getinfo(request: Request):
    return await sdk.handle_callback(request)

@app.post("/login")
async def login(payload: dict):
    jwt = payload["jwt"]
    try:
        return {"success": True, "user": await sdk.get_slot_info(jwt)}
    except AuthorizeFailedError as err:
        status = 401 if err.code == "EXPIRED_TOKEN" else 502
        raise HTTPException(status_code=status, detail=str(err)) from err
```

A more complete example lives in [`examples/fastapi_app.py`](examples/fastapi_app.py).

---

## Backend SDK

### Constructor

```python
AltsCodexBackend(
    *,
    client_id:        str,                       # required
    client_secret:    str,                       # required — stored privately
    redirect_uri:     str,                       # required
    auth_server_url:  str | None = None,         # default: https://api.altscodex.com
    http_client:      httpx.AsyncClient | None = None,  # optional, for reuse / mocking
)
```

### `await sdk.get_slot_info(jwt, *, timeout=15.0) -> SlotInfo`

Runs the full DeOAuth flow: `authorize` → wait for callback → exchange code for slot info.

```python
slot = await sdk.get_slot_info(jwt, timeout=15.0)
```

### `await sdk.handle_callback(request) -> dict`

Process the DeOAuth callback. Accepts a Starlette/FastAPI `Request` object (anything with `.query_params`) or a plain mapping `{"query": {...}}` for tests.

Returns `{"received": True}` immediately; the slot-info exchange runs in the background and resolves the pending `get_slot_info` future.

### `await sdk.shutdown()`

Rejects every pending request and closes the owned HTTP client. Call this from a FastAPI lifespan or shutdown handler.

### `SlotInfo`

```python
class SlotInfo(TypedDict, total=False):
    id:              str | None     # Unique slot ID — use as your user identifier
    access_token:    str | None     # DeOAuth access token
    content_address: str | None     # Blockchain wallet address
    token_nickname:  str | None     # Slot nickname
    tr_cnt:          int | None     # Transfer count
    code:            str | None     # OAuth authorization code
```

### Exceptions & Recommended HTTP Status

| Exception | Recommended HTTP Status |
|-----------|------------------------|
| `AuthorizeCallbackTimeoutError` | 408 |
| `AuthorizeFailedError` (`code == "EXPIRED_TOKEN"`) | 401 |
| `AuthorizeFailedError` (other codes) | 502 |
| `AuthorizeRejectedError` | 502 |
| `ShutdownError` | 503 |
| `AltsCodexHTTPError` | mirrors `.status` |

All exceptions inherit from `AltsCodexError`.

---

## Frontend Helper (server-side)

The real frontend SDK is browser-only (popup + `localStorage` + `postMessage`). The Python helper provides the two pieces that do translate to a server:

```python
from altscodex import AltsCodex

helper = AltsCodex(
    client_id="YOUR_CLIENT_ID",
    redirect_uri="https://yourapp.com/callback",
    # altscodex_url defaults to https://altscodex.com
)

# 1) Build the redirect URL for the browser
login = helper.build_login_url()
return RedirectResponse(login.url)
# store login.state in the user session to verify on callback

# 2) Parse a server-side redirect callback
payload = AltsCodex.parse_callback(request.query_params)
if not payload.success:
    raise HTTPException(401, "login failed")
```

For the full popup-based browser flow, use the JavaScript SDK.

---

## Local Development

Override base URLs for staging or local servers:

```python
sdk = AltsCodexBackend(
    auth_server_url="http://localhost:3000",
    client_id="YOUR_CLIENT_ID",
    client_secret="YOUR_SECRET",
    redirect_uri="http://localhost:3070/getinfo",
)
```

---

## Testing

```bash
pip install -e ".[dev]"
pytest -v
```

The test suite ports all six scenarios from the JavaScript Jest suite plus
constructor and secret-leak contract checks.

---

## Publishing to PyPI

The `altscodex-sdk` name is currently unclaimed on PyPI. Use the bundled
script to claim it and publish releases.

```bash
# 1) Build + twine check only (no upload) — sanity check
scripts/publish.sh --check

# 2) Dry-run upload to TestPyPI first (recommended for the first release)
scripts/publish.sh --test
pip install --index-url https://test.pypi.org/simple/ altscodex-sdk==2.1.0

# 3) Production upload to PyPI — claims the name
scripts/publish.sh
```

**Credentials.** Set up an API token at [pypi.org → Account → API tokens](https://pypi.org/manage/account/token/) and either:

- create `~/.pypirc`

  ```ini
  [pypi]
    username = __token__
    password = pypi-AgEIcHlwa...   # your token

  [testpypi]
    repository = https://test.pypi.org/legacy/
    username = __token__
    password = pypi-AgENdGVzdC5w...   # separate token for TestPyPI
  ```

- or export per-invocation:

  ```bash
  TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-... scripts/publish.sh
  ```

**Bumping versions.** Edit `version = "..."` in `pyproject.toml`. PyPI never lets
you re-upload the same version, so always bump before re-publishing.

---

## Notes on the Port

- **HTTP**: `httpx.AsyncClient` — accepts `http_client=...` for reuse or `httpx.MockTransport`-based testing.
- **Promise → Future**: pending state lives in `asyncio.Future`, with `loop.call_later` for the timeout.
- **`respose_type` typo preserved**: the upstream DeOAuth server expects the misspelled query key. Do not "fix" it.
- **`client_secret`**: stored in a name-mangled attribute (`_AltsCodexBackend__client_secret`) and never exposed on the public surface.
- **Callback acknowledgement**: returned synchronously (`{"received": True}`) so the DeOAuth server's HTTP connection doesn't wait for the `get_token` exchange.

---

## Resources

| Resource | URL |
|----------|-----|
| Platform | [altscodex.com](https://altscodex.com) |
| Developer Center | [developers.altscodex.com](https://developers.altscodex.com) |
| API Server | `https://api.altscodex.com` |
| Blockchain Explorer | [scan.xotown.com](https://scan.xotown.com) |

---

## License

MIT
