# Authentication and session reuse

Modules: `core/auth/settings.py`, `core/auth/session_token_store.py`, `core/auth/session.py`,
`core/auth/injection.py`, `core/auth/ui_login.py`, `core/auth/token_grants.py`,
`core/auth/secrets.py`, `ui/fixtures/session_fixtures.py`.

## Auth settings ladder

### What it does

Every authentication setting is read through the shared configuration ladder
(environment variable > `.env.<env>` > `environments.json` > default; see
[config-env.md](config-env.md)). `resolve_auth_mode(config)` is the only resolver
of the auth mode. When two places resolve the mode in two different ways, one of
them reports a mode the run is not using, and that is usually the report header a
reader trusts most.

The modes are:

| Mode | Meaning |
|---|---|
| `none` (default) | the system under test needs no login |
| `ui` | log in through the hosted login page in a browser |
| `password` | resource-owner password grant against the token endpoint |
| `client_credentials` | machine-to-machine token |
| `token` | a token handed in from outside (`SUPPLIED_TOKEN`), never refreshed |

`AuthSettings` keeps `password`, `client_secret` and `supplied_token` out of its
`repr`, so it is safe to log. `missing_for_mode()` names the variables the chosen
mode needs but does not have.

### How to use it

```python
from core.auth.settings import AuthSettings, resolve_auth_mode

settings = AuthSettings.from_config(config)
if settings.missing_for_mode():
    pytest.skip(f"set {', '.join(settings.missing_for_mode())}")
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `none` | one of the modes above |
| `LOGIN_URL` | `BASE_URL` | where the login starts |
| `POST_LOGIN_URL` | `BASE_URL` | URL that proves the login finished |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | empty | credentials (`ui`, `password`) |
| `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, `AUTH_AUDIENCE`, `AUTH_TOKEN_URL` | empty | token-endpoint settings |
| `AUTH_SCOPE` | empty | space-separated scopes sent with a token grant |
| `AUTH_ORGANIZATION` | empty | entry to pick on an organisation picker; tenant sent with a token grant |
| `AUTH_HEADLESS` | `true` | headless login browser |
| `SUPPLIED_TOKEN` | empty | the token for `AUTH_MODE=token` |
| `AUTH_SESSION_TTL_SECONDS` | `900` | server session lifetime for opaque sessions |

## Log in once, reuse the session

### What it does

A UI login costs seconds per test and puts load on the identity provider.
`login_once(browser, cache, login)` logs in once per run and stores two things:

- **the bearer token**, taken from the traffic rather than scraped from the page:
  the `Authorization: Bearer` header of the app's own calls, or the
  `access_token` in a token-endpoint response (`/token`, `/oauth/token`,
  `/oauth2/token`). If neither appears, it scans `localStorage` and
  `sessionStorage` for a JWT;
- **the Playwright storage state** (cookies and `localStorage`), written
  atomically to the cache. Every later context can then start logged in.

While the cache is still valid, nothing is launched. A cookie-only login (no
token was ever seen) is reused based on the age of its storage state. A login
that had a token is reused only while that token is still usable.

`SessionTokenStore` writes both files atomically (temp file plus `os.replace`, mode
`0600`). The cache is shared by every run and xdist worker on the machine, and
two logins that finish at the same moment must not leave a half-written file. A
storage-state file that does not parse as exactly one JSON object is treated as
absent, so the run logs in again.

### How to use it

The opt-in fixtures in `ui/fixtures/session_fixtures.py` wire this up. Import them
by name into a `conftest.py`:

```python
from ui.fixtures.session_fixtures import auth_storage_state, tagged_context_args  # noqa: F401


@pytest.fixture
def context(browser, browser_context_args, auth_storage_state):
    args = tagged_context_args(browser_context_args, browser, auth_storage_state)
    context = browser.new_context(**args)
    yield context
    context.close()
```

`auth_storage_state` returns `None` for any mode other than `ui`. With
`AUTH_MODE=ui` and credentials missing, it skips with the names of the missing
variables.

To use it directly, with any login routine:

```python
from core.auth.session_token_store import SessionTokenStore
from core.auth.ui_login import login_once
from ui.components.universal_login import UniversalLoginPage

cache = SessionTokenStore(settings.cache_key(), session_ttl_s=settings.session_ttl_s)
result = login_once(
    browser,
    cache,
    lambda page: UniversalLoginPage(page).login(
        settings.login_url,
        settings.username,
        settings.password,
        app_url=settings.post_login_url,
    ),
)
context = browser.new_context(storage_state=str(result.storage_state))
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TEST_AUTH_CACHE_DIR` | `~/.cache/test-auth` | where the token and storage state are kept |

## JWT expiry check

### What it does

`jwt_is_usable(token, margin_s, now)` reads `exp` from the token payload and
accepts the token only while it stays valid past the safety margin (default 60 s).
A token that expires in the middle of a test fails in a way that looks like a
product defect.

The payload is **base64url** (RFC 7515: `-` and `_`, padding removed). Decoding
it needs the URL-safe alphabet and the padding put back. If you swap the
characters to the standard alphabet and then use a URL-safe decoder, every
payload that contains them fails to decode. An unreadable token counts as
expired, so a valid login would be thrown away for a large share of tokens. The
tests cover payloads with `-`/`_`, with `+`/`/`, and every padding length.

Other rules:

- a JWT without an `exp` claim is usable;
- an unreadable JWT is not usable;
- an **opaque** session token has no expiry, so its freshness is the age of the
  storage-state file compared with the session lifetime, minus the margin.

### Example

```python
from core.auth.session_token_store import jwt_is_usable, jwt_expiry

jwt_expiry(token)  # 1767225600.0, or None
jwt_is_usable(token, margin_s=60)  # False within the last minute
```

## Session refresh

### What it does

Access tokens often live for minutes, while a sequential browser run can take
most of an hour. `SessionToken.token()` returns the token it holds while that
token stays valid past `refresh_margin_s` (default 120 s). Otherwise it reuses a
cached token if one is still valid, and if not, it calls the acquire function
again. A token handed in from outside (`AuthMode.TOKEN`) is never refreshed.
Swapping it silently would hide the fact that it expired.

Resolve the token **before** you build the browser context. The context loads the
cached storage state only while that state is fresh, so a refresh that happens
after the context exists leaves the page unauthenticated.

### How to use it

```python
from core.auth.session import SessionToken

session = SessionToken(
    acquire=fetch_token, cache=cache, mode=settings.mode, supplied_token=settings.supplied_token
)
headers = {"Authorization": f"Bearer {session.token()}"}
```

## Putting a saved login into an existing context

`apply_session(context, page, token, cache)` authenticates a context that was
created without the login. It returns what it did: `local_storage`, `cookies` or
`none`.

- **JWT** -> written to `localStorage` under `access_token` and `token`. The
  token is passed to `page.evaluate` as an argument and never formatted into the
  script text.
- **Opaque token** -> the cached cookies are copied **exactly as issued**
  (domain, path, `httpOnly`, `secure`, expiry, `sameSite`). A hand-built
  anti-forgery cookie on the wrong host cannot be read by the page, so its first
  write is refused.

```python
from core.auth.injection import apply_session

assert apply_session(context, page, session.token(), cache) != "none"
```

## Token grants without a browser

### What it does

`core/auth/token_grants.py` gets a bearer token for the three non-browser modes,
dispatching on the mode the settings ladder resolved, so the code that
authenticates and the report header can never disagree:

- **`password`** — the OAuth 2.0 resource-owner password grant (RFC 6749 §4.3):
  `grant_type=password`, `username`, `password`, `client_id`, plus
  `client_secret`, `audience`, `scope` and `organization` when set. This grant is
  **legacy**: OAuth 2.1 removes it and many identity providers disable it per
  client. It is opt-in only. When the provider refuses it, the error carries the
  provider's own `error` code (`unauthorized_client`, `access_denied`), so a
  disabled grant is recognisable at once.
- **`client_credentials`** — machine-to-machine (RFC 6749 §4.4) with the client id
  and secret, plus `audience`, `scope` and `organization` (tenant) when set. A blank
  secret is looked up in the OS credential store (see the next section).
- **`token`** — a token acquired elsewhere and handed in through `SUPPLIED_TOKEN`.
  It is checked **before any request**: it must be a readable JWT and stay valid
  beyond a 60-second margin. Without that check an expired token turns every test
  into a 401 that reads like a product defect. A JWT without `exp` passes, and an
  unreadable `exp` is refused.

`ui` mode needs a browser and is handled by `login_once` (above);
`resolve_token` says so instead of guessing. Token requests go through the shared
`HttpEngine` as form posts, so they carry the run-id tag. No error message or log
line contains a password, secret or token.

`is_authorised_for(token, url)` probes a service with the token and returns
False on 401/403, because "a token was issued" and "this token opens that
service" are different claims.

### How to use it

```python
from core.auth.session import SessionToken
from core.auth.settings import AuthSettings
from core.auth.session_token_store import SessionTokenStore
from core.auth.token_grants import resolve_token

settings = AuthSettings.from_config(config)
session = SessionToken(
    acquire=lambda: resolve_token(settings),
    cache=SessionTokenStore(settings.cache_key()),
    mode=settings.mode,
    supplied_token=settings.supplied_token,
)
engine = HttpEngine(base_url, token=session.token())
```

`SessionToken` adds caching and refresh-before-expiry for the two grant modes.

### Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `AUTH_MODE` | all | `password`, `client_credentials` or `token` |
| `AUTH_TOKEN_URL` | grants | the token endpoint (full URL) |
| `AUTH_CLIENT_ID` | grants | OAuth client id |
| `AUTH_CLIENT_SECRET` | grants | client secret; for `client_credentials` it may come from the credential store |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | `password` | resource owner |
| `AUTH_AUDIENCE`, `AUTH_SCOPE`, `AUTH_ORGANIZATION` | grants | sent only when set |
| `SUPPLIED_TOKEN` | `token` | the injected JWT; pass it as an environment variable, never on a command line a CI console echoes |

### Example

```
AUTH_MODE=client_credentials AUTH_TOKEN_URL=https://id.example.test/oauth/token \
AUTH_CLIENT_ID=svc-tests pytest -m api      # secret from the credential store

AUTH_MODE=token SUPPLIED_TOKEN="$(get-token)" pytest -m api
# expired:  TokenGrantError: SUPPLIED_TOKEN expired (exp 2026-01-01T10:00:00+00:00); acquire a fresh one
```

## Secrets from the environment or the OS credential store

### What it does

`core/auth/secrets.py` looks a secret up in a fixed order: the environment
variable first, then the operating system's credential store (macOS Keychain,
Windows Credential Locker, Secret Service) through the optional `keyring`
package. `keyring` is imported only when the environment is empty, so CI never
needs it. When it is needed but not installed, or the secret is not stored,
`require_secret` raises `SecretNotFound` with a message that says exactly what to
set or run. A locked or missing store backend counts as "not found".

### How to use it

```python
from core.auth.secrets import lookup_secret, require_secret

secret = lookup_secret("AUTH_CLIENT_SECRET")  # None when nowhere
secret = require_secret("AUTH_CLIENT_SECRET")  # raises SecretNotFound with guidance
```

Store a secret once:

```
pip install keyring
keyring set test-automation AUTH_CLIENT_SECRET
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `<NAME>` | — | the secret itself; always wins |
| `AUTH_KEYRING_SERVICE` | `test-automation` | service name in the credential store |

## Auth-mode precedence, pinned

`tests/core/auth/test_auth_mode_resolution.py` sets every rung in-process (real
environment variable through `monkeypatch.setenv`, a real `.env.qa` file, a real
`environments.json`) and pins the ladder: environment > `.env.<env>` > committed
value > default; a blank value at any rung is ignored; matching ignores case and
treats `-` as `_`. An unknown value **fails**, naming the valid modes, rather
than falling back to some default flow — a misspelled mode must never silently
select a different login.

## Storage-state atomic write, pinned

`tests/core/auth/test_storage_state_atomic_write.py` guards the cached browser
storage state: a short state written over a long one leaves exactly the short one
(temp file plus `os.replace`, no tail of the old file), a file with trailing bytes,
a non-object or a missing file is reported unreadable before it can reach
`browser.new_context`, and eight threads writing through separate cache instances
while a reader polls never produce a file that fails to parse or leave a temp file
behind.
