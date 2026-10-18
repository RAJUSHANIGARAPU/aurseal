# Fault injection

`checks/faults/` injects failures into what a client sees — in the browser through Playwright
routing, on the API side through a faulty transport under the project's `HttpEngine` — and
checks that the client handles them. It replaces the chaos-resilience plugin, which asked a
model for scenario prose and reported `pass` when the model replied.

## What it does

* **One vocabulary.** `FaultSpec(kind, url_glob, method=None, probability=1.0, after_n=0,
  times=None, latency_ms=0, status=503, id="")` with kinds `latency`, `status`, `abort`
  (connection reset), `timeout`, `truncate_body`. `url_glob` uses Playwright's shape (`**` any,
  `*` anything but `/`); the same matcher decides on both sides.
* **Window and replay.** Matching requests are numbered from 0; a fault fires from number
  `after_n`, at most `times` times, each time with `probability` drawn from a seeded generator,
  so a failing run replays exactly. Each spec keeps its own window; the first eligible spec is
  applied.
* **Browser.** `inject(context_or_page, specs, seed=0) -> FaultHandle` installs a route handler:
  `status` fulfils with the status and a JSON body, `abort` aborts with `connectionreset`,
  `timeout` with `timedout`, `latency` waits then lets the request through, `truncate_body`
  fetches the real response and returns half its body. Unmatched requests fall through.
* **API.** `FaultyTransport(inner_session, specs, seed=0)` is a drop-in `requests` session:
  `HttpEngine(base_url, session=FaultyTransport(...))`. `status` returns without sending,
  `abort` raises `requests.ConnectionError`, `timeout` raises `requests.Timeout`.
* **Marked.** Every injected response carries `x-fault-injected: <spec id>`. The circuit
  breaker (`gate.circuit_breaker`) ignores responses with that header when it is given the
  headers (`record(..., headers=...)`), so a manufactured 503 never trips it; a real 503 still
  does.
* **Oracles** (`checks.faults.oracles`, each returns `OracleResult`): `no_blank_page(page)`,
  `error_is_shown(locator)`, `no_uncaught_page_errors(evidence.page_errors)`,
  `retries_bounded(handle, max_n)`, `recovers_after(handle, action, check)`.
* **The `faults` fixture** (`checks.faults.pytest_plugin`): `faults.browser(...)`,
  `faults.api(...)`, `faults.note(oracle_result)`. At teardown it removes every fault and writes
  one `metric_fault_injection` row per spec (fired count, oracles). **A test that passed while
  no injected fault fired is reported as skipped, `held_empty`**: a resilience test whose
  fault never happened proved nothing.
* **Plugin.** `fault-injection` (tier 3) runs `pytest -m chaos` on the chaos examples and maps
  the result: all passed ⇒ `pass`; every test skipped (no fault exercised) ⇒ `unknown`;
  failures ⇒ `fail`; harness errors ⇒ `error`; nothing collected ⇒ `unknown`.

## How to use

```python
import pytest, requests
from api.clients.http_engine import HttpEngine
from checks.faults.oracles import error_is_shown, recovers_after
from checks.faults.spec import FaultSpec

@pytest.mark.chaos
def test_orders_screen_survives_a_503(page, faults):
    handle = faults.browser(page, [FaultSpec("status", "**/api/orders", method="GET")])
    page.goto(url)
    faults.note(error_is_shown(page.get_by_test_id("error-banner"))).check()
    faults.note(recovers_after(handle, page.reload, lambda: page.get_by_role("table").is_visible())).check()

@pytest.mark.chaos
def test_client_retries_after_a_reset(faults):
    transport = faults.api(requests.Session(), [FaultSpec("abort", "**/api/orders", times=1)])
    engine = HttpEngine(base_url, session=transport)
    ...
```

Load the fixture with `-p checks.faults.pytest_plugin`, or import `faults`,
`pytest_runtest_makereport` and `pytest_configure` from it in a `conftest.py` (that is how
`tests/demo_examples/chaos/conftest.py` does it).

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `seed` | 0 | seeds the probability draws; same seed, same faults |
| `METRICS_DIR` | `reports/metrics` | where `metric_fault_injection` rows go |
| marker `chaos` | registered by the plugin | selects fault-injection tests |
| plugin `paths` / `marker` | `tests/demo_examples/chaos`, `chaos` | what the plugin runs |

## Example

* `tests/demo_examples/chaos/test_api_faults.py` (offline, demo service on localhost): an
  injected 503 is marked and does not trip the breaker; a real 503 from the demo's
  `api_unavailable` switch is not marked and does (control); an aborted call raises and the next
  succeeds; latency still returns the right answer; the flow recovers once the fault is removed.
* `tests/demo_examples/chaos/test_browser_faults.py` (`demo_ui`): the subscription list with
  `POST /api/search` answering 503, or the connection reset, shows its error banner, is not
  blank, did not hammer the API, and comes back after a reload once the fault is removed; with
  600 ms latency the list still renders.

## Honest limits

Routing-level injection tests how the *client* handles failure, not how a server degrades under
real resource exhaustion (that would need network-level tools such as a TCP proxy, which were
not chosen). The demo's resilience behaviour (an error banner) was designed in; a screen with no
error handling has nothing for the oracles to find.
