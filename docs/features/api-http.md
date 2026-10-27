# HTTP layer

Every API call a test makes goes through one transport, `HttpEngine`
(`api/clients/http_engine.py`). It handles the things that must be the same on
every request: joining the base URL, auth headers, run tagging, timeouts,
multipart upload, binary download, safe retry of versioned writes, and
observers that see every response.

## HttpEngine

**What it does.** A thin wrapper over a `requests.Session` that returns typed
`ApiResponse` objects.

**How to use it.**

```python
from api.clients.http_engine import HttpEngine

api = HttpEngine("https://api.example.test/v1/", token=my_token)
r = api.get("items", params={"q": "lamp"})
created = api.post("items", json={"name": "Desk lamp"})
api.put(f"items/{created.location_id}", json={"name": "Floor lamp"})
api.delete(f"items/{created.location_id}")
```

- A relative path is joined to the base URL. An absolute `http(s)://` URL is
  used as given.
- `token=` sends `Authorization: Bearer <token>`. `headers=` adds or overrides
  any header on every request (for example a cookie or a CSRF header). A
  per-call `headers=` argument overrides both.
- `observe(callback)` registers `callback(method, url, response)`, which runs
  after every response. A callback that raises is logged and never fails the
  request. The cleanup ledger uses this hook (see `test-data.md`).
- `session=` injects a `requests.Session`. Tests mount an offline adapter on
  it, so no network is needed.

**Configuration.**

| Setting | Default | Meaning |
|---|---|---|
| `API_TIMEOUT_S` (env) | `60` | Per-request timeout in seconds (the `timeout_s=` argument overrides it) |
| `Accept` header | `application/json` | Overridable through `headers=` |

## ApiResponse

**What it does.** An immutable record of one response: `status`, raw
`content`, `headers` and `url`.

| Member | Returns |
|---|---|
| `is_success` | `True` for 2xx |
| `text` / `json()` | The body as text, or parsed JSON (`ValueError` on an empty body) |
| `header(name)` | Case-insensitive header lookup |
| `location` / `location_id` | The `Location` header, and the id in it (last UUID, else last path segment) |
| `raise_for_status()` | Raises `ApiError` (an `AssertionError`) carrying the response |
| `str(response)` | `HTTP <status> <url> <body>`, with the body cut to 800 characters for assertion messages |

```python
r = api.post("orders", json={"sku": "A-1"})
assert r.is_success, r  # failure message shows status, URL and body
order_id = r.location_id
```

## Multipart upload and binary download

```python
from api.clients.http_engine import Part

api.post_multipart(
    "documents",
    [
        Part.json("meta", {"title": "Quarterly report"}),
        Part.file("file", "report.pdf", "application/pdf", pdf_bytes),
    ],
)

pdf = api.get_bytes("documents/42/content")  # raises ApiError on non-2xx
```

`get_bytes` sends `Accept: application/octet-stream` by default. Pass
`accept=` to change it.

## Run tagging

**What it does.** Tags every API request as test traffic, with one id per run.
Each request carries an `X-Test-Run-Id` header and a `User-Agent` product token
that contains the same id. Operators can then tell test traffic from real users
in gateway and service logs, and a test's own requests can be found again
afterwards. In the browser the token rides on the end of the User-Agent,
never an extra header: a custom header on a cross-origin call would trigger a
CORS preflight on every request.

**Configuration.**

| Env var | Default | Meaning |
|---|---|---|
| `TEST_RUN_ID` | (unset) | The run id. It wins over everything else. |
| `BUILD_TAG`, `GITHUB_RUN_ID` | (unset) | CI build ids, used when `TEST_RUN_ID` is not set |
| `TEST_RUN_TAGGING` | `true` | `false` / `0` / `no` / `off` turns tagging off |

Without any of these the id is `local-<host>-<yyyymmdd-HHMMSS>`. Ids are made
header-safe (characters outside `[A-Za-z0-9._-]` become `-`) and are capped
at 64 characters. The id is resolved once per process. Under `pytest -n auto`
each worker is its own process, so set `TEST_RUN_ID` when one id must span
every worker.

```bash
TEST_RUN_ID=nightly-2031-05-04 pytest -m api -n auto
```

## Safe retry of versioned writes

**What it does.** Retries a write that carries an optimistic-lock version
(`?version=N`) after a gateway error, but only when retrying is provably safe.

A versioned write either commits and moves the entity past `N`, or changes
nothing. A duplicate sent with the same `N` is refused with 409 rather than
applied twice. The rules:

1. Only HTTP 502/503/504, or a read timeout (the request was sent and got no
   answer), trigger the retry logic. A 500 or any 4xx is returned as-is.
   A connect timeout means the request never left, so it propagates.
2. After the error the engine waits (linear backoff), then re-reads the entity.
   **If the version has moved past `N`, the write landed. The fresh entity is
   returned and the write is never sent again.**
3. If the version is still `N`, or the entity cannot be read, the write is
   re-sent with the same `N`, up to `max_retries` times.
4. A 409 on a retry gets the same re-read: it is what a delayed original
   request that already went through looks like.

Calls without a version token, and calls the matcher does not recognise, are
never retried.

**How to use it.**

```python
from api.clients.http_engine import HttpEngine
from api.clients.versioned_retry import query_version_matcher

api = HttpEngine(
    "https://api.example.test/v1/",
    versioned=query_version_matcher(exclude=("/preview/",)),
    version_field="version",  # JSON field holding the entity version
    max_retries=2,
    backoff_s=5.0,
)
api.put("entities/3f2a.../actions/rename?version=7", json={"name": "B"})
```

`query_version_matcher(entity_pattern=..., param="version", methods=...)`
recognises `PUT`/`POST`/`PATCH` calls to `<entity>/<action>?version=N`. The
named regex group `entity` is the path that gets re-read. Any callable
`(method, url) -> VersionedTarget | None` can be used instead.

| Argument | Default | Meaning |
|---|---|---|
| `versioned` | `None` (off) | Matcher that enables the retry |
| `version_field` | `"version"` | Field read from the re-read entity |
| `max_retries` | `2` | Re-sends after the first attempt (`0` still checks whether it landed) |
| `backoff_s` | `5.0` | Waits `backoff_s × attempt` before each re-read |

## Gateway no-route vs service 404

**What it does.** Tells a gateway's own "no route matched" 404 apart from a
404 that a service behind the gateway returned. Both carry the same status, so
only the body shape says whether the route exists. `classify_not_found`
(`api/clients/route_probe.py`) returns one of:

| Verdict | Meaning |
|---|---|
| `GATEWAY_NO_ROUTE` | The gateway's default error body: no route for this path |
| `SERVICE_NOT_FOUND` | A problem detail from a service: the route exists, the resource does not |
| `UNRECOGNISED` | A 404 whose body matches neither shape (empty, HTML, another layout) |
| `NOT_A_404` | Any other status; nothing to judge |

`require_measured` guards the probe. A 401, or a plain-text 403 from a firewall
in front of the gateway, means the request was never routed. It raises
`UnmeasuredResponse` instead of letting a login problem read as a missing
route. A 403 with a JSON body is a real answer and passes.

**How to use it.**

```python
from api.clients.route_probe import RouteVerdict, classify_not_found, require_measured

r = require_measured(api.get("orders/not-an-id"))
assert classify_not_found(r) is RouteVerdict.SERVICE_NOT_FOUND, f"route vanished: {r}"
```

**Configuration.** Pass a `NotFoundShapes` when the gateway words its default
error differently. There are no environment variables.

| Field | Default | Meaning |
|---|---|---|
| `gateway_keys` | `{"requestId", "path"}` | Non-null keys every gateway body has |
| `error_key` / `error_text` | `"error"` / `"Not Found"` | Field and value the gateway body carries; `error_text=None` skips the check |
| `service_keys` | `{"title"}` | Keys of a service problem detail; their presence rules out the gateway shape |

```python
from api.clients.route_probe import NotFoundShapes

shapes = NotFoundShapes(
    gateway_keys=frozenset({"trace"}),
    error_key="code",
    error_text="NO_ROUTE",
    service_keys=frozenset({"reason"}),
)
classify_not_found(r, shapes)
```

## Write-route probe without side effects

**What it does.** `probe_write(engine, method, path, body, *, control,
positive, accept)` proves a new write route reaches its service with a
request the service must refuse before changing anything (a random id, an
invalid body). `accept` is a predicate on the service's own refusal; build it
with `service_refusal(statuses, keys=..., body_contains=...)`. The
`control` (`(method, path)` outside the route predicate) must be answered by
the gateway's own no-route body, and `positive` (a GET on a known routed path)
must answer 2xx. The result is a `WriteProbe` with one `WriteVerdict`:
`REACHES_SERVICE` is the only pass; an accepted probe is `SIDE_EFFECT`.

`probe_head(engine, path_for, create, discard)` proves a body-less method on
a resource the test created: HEAD answers 200, the resource is discarded
(even when the first HEAD raises), HEAD answers 404.

Both skip on a read-only environment (`gate.read_only.require_writable`,
reading the profile's `read_only`); pass `read_only=False` to state it.

```python
from api.clients.route_probe import probe_write, service_refusal

probe_write(
    engine,
    "POST",
    "gw/tasks",
    {},
    control=("POST", "gw/tasksx"),
    positive="gw/tasks",
    accept=service_refusal({422}, keys=("fields",), body_contains=("title",)),
).require()
```

