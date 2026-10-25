# Browser evidence, artifacts and run diagnostics

Modules: `core/browser_evidence.py`, `core/artifact_policy.py`, `core/bounded_close.py`,
`core/failure_reporter.py`, `core/build_info.py`, `core/provenance.py`,
`core/browser_resilience.py`, `core/network_recorder.py`.

## Evidence capture

### What it does

`BrowserEvidence` collects what the browser reported while a test ran:

- console messages of type `error`;
- uncaught page exceptions (`pageerror`);
- failed requests: `METHOD url -> status` for HTTP 4xx/5xx, and
  `METHOD url -> <failure>` for requests that never got a response (DNS errors,
  resets, aborts).

It listens on the **context**, so every page is covered, including popups and
tabs the app opens. Each list keeps at most 200 entries, so a page that logs an
error on every frame cannot make the bundle unbounded. `screenshot(page, name)`
saves a full-page PNG under `reports/screenshots/` and keeps the bytes for the
failure bundle.

### How to use it

In the root `conftest.py`, this replaces the module-level lists:

```python
from core.browser_evidence import BrowserEvidence

_EVIDENCE = pytest.StashKey[BrowserEvidence]()


@pytest.fixture(autouse=True)
def collect_browser_evidence(context, request):
    request.node.stash[_EVIDENCE] = BrowserEvidence().attach_context(context)
    yield
```

Or ask for the opt-in `browser_evidence` fixture from `ui/fixtures/session_fixtures.py`.

## Trace and video retention

### What it does

The trace and video of a test are kept only when someone is likely to look at them:

| Outcome | Trace | Video |
|---|---|---|
| failed (setup or call) | saved | kept |
| skipped from inside the test (`pytest.skip` in the body) | saved | kept |
| expected failure (`xfail`, `known_defect`) | discarded | deleted |
| passed | discarded without serialising | deleted |

A snapshot trace of a long browser test can be hundreds of megabytes and takes
real time to write. Nobody opens the trace of a passing test.

Teardown has to know the outcome, which pytest does not give to fixtures.
`OutcomeRecorder` is a `pytest_runtest_makereport` hook that stores each phase's
report on the item. `worth_keeping(request.node)` then answers the question in
fixture teardown.

### How to use it

```python
# conftest.py, inside pytest_configure(config)
from core import artifact_policy

artifact_policy.register(config)
```

```python
from core.artifact_policy import (
    RetentionPolicy,
    finalize_videos,
    finish_trace,
    page_videos,
    worth_keeping,
)


@pytest.fixture(autouse=True)
def enable_artifacts(context, request, browser_name):
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    yield
    policy = RetentionPolicy.from_env()
    keep = worth_keeping(request.node)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = request.node.name
    finish_trace(
        context, policy.keep_trace(keep), artifact_names.trace_path(name, browser_name, stamp)
    )
    videos = page_videos(context)  # read the handles before closing
    context.close()  # closing flushes the video files
    finalize_videos(
        videos,
        policy.keep_video(keep),
        [
            artifact_names.video_path(name, browser_name, stamp, i, len(videos))
            for i in range(len(videos))
        ],
    )
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TRACE_ALWAYS` | `false` | keep every trace (evidence run) |
| `VIDEO_ALWAYS` | `false` | keep every video |

## Bounded teardown

### What it does

`Browser.close()` and `BrowserContext.close()` wait for a reply from the driver.
When the browser process has died, that reply never comes and the call waits
forever. Nothing is raised, so a `try/except` does not help, and only a deadline
does. Both helpers log failures at WARNING and never raise:

- `close_within(label, action, timeout_s)` runs the close on a **daemon** thread
  and gives up on it at the deadline. Daemon matters: a non-daemon thread would
  move the hang from the start of the run to its exit.
- `aclose_within(label, close, timeout_s)` does the same for the **async** API
  through `asyncio.wait_for`.

**Limit with sync Playwright.** Sync API objects are tied to the thread that
created them and refuse calls from any other thread. `close_within` therefore
cannot put a deadline on a sync `browser.close()`. For sync code, set the bound
at the process level (for example `pytest-timeout`). `close_within` still covers
the other resources a teardown releases, such as HTTP sessions, tunnels or
recorder processes.

### Example

```python
from core.bounded_close import aclose_within, close_within

close_within("tunnel", tunnel.stop)  # thread-safe resource
await aclose_within("browser", browser.close, timeout_s=5)  # async Playwright
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PLAYWRIGHT_CLOSE_TIMEOUT_MS` | `10000` | deadline; non-numeric or <= 0 falls back to the default |

## Failure bundle with the real stack

### What it does

`write_failure_bundle(...)` writes `reports/failures/<test>-<timestamp>.json` with
these fields: the error, `errorType`, the stack trace, a base64 screenshot,
console errors, page errors, failed requests, a DOM snapshot and a free-form
`context` (for example the run id and the deployed version).

The stack trace is taken from the exception itself. `traceback.format_exc()`
only knows about an exception that is being handled right now, and the bundle is
written from a report hook after the `except` has finished, so it used to record
`NoneType: None` for every failure. Long traces keep the **last** 4000
characters, because that is where the failing frames are.

### How to use it

In `pytest_runtest_makereport` of the root `conftest.py`:

```python
evidence = item.stash.get(_EVIDENCE, None)
from core import run_id
from core.build_info import last_observed

write_failure_bundle(
    test_name=item.name,
    error=call.excinfo.value,
    screenshot_bytes=screenshot_bytes,
    console_errors=list(evidence.console_errors) if evidence else [],
    failed_requests=list(evidence.failed_requests) if evidence else [],
    page_errors=list(evidence.page_errors) if evidence else [],
    dom_snapshot=dom_snapshot,
    context={"runId": run_id.run_id(), "deployedVersion": last_observed().describe()},
)
```

## Deployed-version probe

### What it does

The probe asks the environment under test which build it is running, once per
process, and records the answer. A diagnosis that names a cause ("still on the
old build") when the run never asked is worse than no diagnosis.

`BuildInfoProbe(fetch, candidates)` tries the candidate endpoints in order
and returns the first real answer. The default candidates are `/version`
(`version` or `versionTag` field) and Spring Boot `/actuator/info`
(`build.version`, with the git commit as detail). If nothing answers, the result
is an explicit `UNKNOWN` that records why each candidate failed: an HTTP status,
an unparseable body, no version field, or unreachable. It never raises and never
invents a version.

`observe_once(probe)` measures once and logs `[version] deployed version: ...`.
`last_observed()` returns the measurement without making an HTTP call. If no
probe has run yet, it says "not measured".

### Example

```python
from core.build_info import BuildInfoProbe, observe_once, requests_fetch

version = observe_once(BuildInfoProbe(requests_fetch("https://api.example.test")))
version.describe()
# "4.2.0 [version endpoint (GET /version)] — commit 1a2b3c4d"
# or "UNKNOWN — /version -> HTTP 404; /actuator/info -> HTTP 403"
```

Add your own endpoint with `Candidate("/health", reader)`. A reader takes the
parsed JSON and returns a `BuildInfo`, or `None`.

## Checkout provenance

### What it does

When the suite reads from a second checkout (a shared spec repo, a configuration
repo), `CheckoutProvenance` records which version of it was read. Without that,
two runs a week apart look identical.

- **where:** `<PREFIX>_PATH` takes priority and does **not** fall back to the
  sibling directory. Otherwise it uses `../<default_sibling>` if that directory
  is a git repo.
- **ref:** `not bound` when nothing was found, `pinned checkout` when
  `<PREFIX>_PINNED=true`, otherwise `<PREFIX>_REF` (default `origin/main`).
- **commit:** `<PREFIX>_COMMIT` passed in by CI comes first, because CI
  containers often have no `git`. Otherwise it uses `git rev-parse` in the
  checkout. Shortened to 8 characters.

### Example

```python
from core.provenance import CheckoutProvenance

specs = CheckoutProvenance(
    prefix="SPEC_REPO", default_sibling="shared-specs", marker_file="openapi.yaml"
)
specs.as_environment("Specs")  # {"Specs.Ref": "origin/main", "Specs.Commit": "0123abcd"}
```

To get these rows into the Allure Environment panel:
`register_report_metadata(config, extra_rows=[lambda: specs.as_environment("Specs")])`.

## Gateway retry wiring

### What it does

`core/browser_resilience.py` contains only the Playwright plumbing for retrying a
browser request that hit a transient gateway error. A route handler fetches the
request, asks an injected policy whether the response deserves another attempt,
retries with linear backoff (`backoff_s * attempt`), and passes the final
response to the page.

The policy is injected:

- `protects(method, url)` decides whether the request goes through the retry at
  all. Exclude downloads and streams, because buffering them through
  `route.fetch()` corrupts or hangs them.
- `should_retry(method, url, status)` decides whether a response is worth
  another attempt.

Two rules belong to the plumbing itself, because they are about never sending a
write twice:

- if `route.fetch()` fails, a GET is resumed and anything else is **aborted**;
- if fulfilling the page fails after the fetch, the route is aborted.

### Example

```python
from core import browser_resilience

browser_resilience.install(
    page,
    protects=lambda method, url: method == "GET" and "/download" not in url,
    should_retry=lambda method, url, status: status in (502, 503, 504),
    max_retries=3,
    backoff_s=1.5,
)
```

With the run-gate retry policy:

```python
from gate.http_retry import Decision, RetryPolicy

policy = RetryPolicy.from_env()
browser_resilience.install(
    page,
    protects=policy.protects,
    should_retry=lambda m, u, s: policy.decide(m, u, s, 0) is Decision.RETRY,
    max_retries=policy.max_retries,
    backoff_s=policy.backoff_s,
)
```

`max_retries <= 0` installs nothing.

## Network call recording around a UI action

### What it does

`core/network_recorder.py` answers "which backend calls did that click cause?".
`NetworkRecorder` listens on a page or a whole context from `start()` to `stop()`
(or for the body of a `with` block) and keeps every matching `Call(method, url,
status)` in the order the browser saw it. It complements the evidence above
(which keeps only what went wrong) and `ResponseCapture` (which keeps the body of
one named call).

Requests and responses are kept apart:

- `requests()` — every matching request sent;
- `responses()` — every matching response, with its status;
- `unanswered()` — requests with no response, matched one-for-one by method and URL
  (aborted, blocked, or still in flight when the action returned);
- `failed()` — requests the browser reported as failed, with the failure text;
- `methods_and_paths()` — `(METHOD, /path)` pairs for a compact assertion;
- `with_status(pred)` — responses whose status matches, e.g. `lambda s: s >= 500`;
- `describe()` — a readable block for an assertion message.

The match is a substring, a compiled regex or a callable on the URL. The number of
calls kept is capped (`limit`, default 1000) and `dropped` counts the rest. `stop()`
never raises, so it is safe in a `finally` after the page closed.

### How to use it

```python
from core.network_recorder import NetworkRecorder

with NetworkRecorder(page, match="/api/") as calls:
    page.get_by_role("button", name="Save").click()
    page.wait_for_load_state("networkidle")

assert calls.methods_and_paths() == [("PUT", "/api/orders/ORD-42")], calls.describe()
assert not calls.with_status(lambda s: s >= 400), calls.describe()
assert not any("/export" in c.url for c in calls.requests())
```

Record on the context instead of the page to include popups and new tabs:
`NetworkRecorder(context, match=re.compile(r"/api/v\d+/"))`.

### Configuration

None; `match` and `limit` are arguments.
