# Demo system under test

`demo_sut/` is an invented subscription and billing service that the example
suites run against: customers, partners, plans, subscriptions with versions and
drafts, a billing ledger with invoices and journal entries, scheduled jobs, PDF
documents, an outbound message box and a few admin screens. It is standard
library only (WSGI, `sqlite3`, `json`) and starts in-process in well under a
second. The full resource model, endpoint list, screens and fault catalogue are
in [`demo_sut/README.md`](../../demo_sut/README.md).

## Running the demo

**What it does.** Serves the JSON API under `/api`, a gateway under `/gw` and
server-rendered screens under `/ui` on a localhost port, backed by one SQLite
file. A fresh database is seeded with two plans, one reseller with its reseller
terms, the ledger accounts and two users; customers and subscriptions are
always created by the test that needs them.

**How to use it.**

```bash
python -m demo_sut --port 8765                 # fresh temporary database
python -m demo_sut --port 0 --db demo.sqlite   # any free port, named database file
```

From Python: `DemoServer()` (a context manager) starts it in a thread on a free
port; `DemoClient(base_url)` is a small standard-library client with the control
calls (`freeze`, `shift`, `set_faults`, `settings`, `reset`).

**Configuration.** `--host` (default `127.0.0.1`), `--port` (default `8765`,
`0` = any free port), `--db` (default: a new temporary file), `--verbose` (log
every request). Behaviour settings are changed at runtime through
`PATCH /api/_admin/settings` (scheduler mode, send order, message id scheme,
day-count convention, index percentage, time zone of the screens, ...).

**Example.**

```python
from demo_sut import DemoClient, DemoServer

with DemoServer() as server:
    client = DemoClient(server.base_url)
    print(client.ok("GET", "/api/plans"))
```

## Pytest fixtures

**What it does.** `demo_sut.pytest_plugin` starts one demo per test session
(so one per xdist worker, each on its own port with its own database), gives a
test control over the clock, fault switches and settings, and puts all of that
back after the test.

**How to use it.** Load with `-p demo_sut.pytest_plugin`, or import the
fixtures in a conftest:

```python
from demo_sut.pytest_plugin import demo_control, demo_http, demo_seed, demo_sut  # noqa: F401
```

* `demo_sut` (session) — handle with `base_url`, `api_url`, `client`, `db_path`.
* `demo_control` (function) — `freeze(instant)`, `shift(days=...)`,
  `faults(*names)`, `fault(name, on=True)`, `settings(**changes)`,
  `reset_database()`. Teardown restores the real clock, no faults and default
  settings, and records the fault set seen during the test in the test's
  `user_properties` as `demo_faults`.
* `demo_seed` (function) — `customer()`, `partner()`, `subscription(state=...)`,
  `book_due()`, `transactions()`, `draft()`, `confirm_draft()`.
* `demo_http` (function) — the project's `HttpEngine` on the demo's `/api/`,
  whose responses feed `demo_control`'s fault evidence.

**Configuration.** `DEMO_SUT_URL` (unset by default): when set, no server is
started and the fixtures point at that running instance (for a CI job that
starts `python -m demo_sut` as a service). Tests sharing one external instance
must not rely on an empty database.

**Example.**

```python
def test_closure_credit_is_bounded(demo_control, demo_seed):
    demo_control.freeze("2026-03-10T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    draft = demo_seed.draft(sub["id"], "cancel", effective_date="2026-03-16", reason="BUDGET")
    demo_seed.confirm_draft(draft["id"])
    closure = next(t for t in demo_seed.transactions(sub["id"]) if t["kind"] == "CANCEL")
    assert 0 < -closure["total"] <= 39.0
```

## Settable clock

**What it does.** Every date the demo writes (periods, invoices, documents,
message ids, audit rows) comes from one clock that follows real time, a shifted
real time, or a frozen instant. Each response reports it in `X-Demo-Clock`
(`2026-03-10T09:00:00.000Z; frozen`).

**How to use it.** `demo_control.freeze("2026-03-10T09:00:00Z")`,
`demo_control.shift(days=30)`, or `POST /api/_admin/clock` with `now`,
`shift_days` / `shift_seconds` or `reset`.

**Configuration.** None; the clock starts in real time.

**Example.** Freeze the clock one day after a period start, book what is due
(`POST /api/jobs/run`), and the first invoice's reference date is that day.

## Fault switches

**What it does.** Twenty-three invented defects (wrong rounding, a money line printed
raw, a credit taxed twice, a payment replay counted twice, an outage, ...) that are
off by default. The active set is reported on every response in `X-Demo-Faults`
and on every screen in the fault banner (`data-testid="demo-faults-banner"`,
attribute `data-faults`), and `demo_control` records it per test, so a detector's
evidence always says whether it ran against a healthy or a deliberately broken
demo — a detector cannot pass by construction unnoticed.

**How to use it.** `demo_control.fault("rounding_drift")`,
`demo_control.faults("a", "b")` (exactly these), `demo_control.faults()` (none),
or `POST /api/_admin/faults` with `{"rounding_drift": true}` /
`{"active": [...]}`. `GET /api/_admin/faults` lists every switch with its
description. An unknown name is a 400 that lists the known ones.

**Configuration.** None; all switches start off and every `demo_control` test
ends with all of them off.

**Example.**

```python
def test_period_charge_format_detector(demo_control, demo_seed, demo_sut):
    demo_control.fault("doc_amount_unformatted")
    sub = demo_seed.subscription(start_date="2026-06-01")
    # ... fetch the account-opened letter; the response header says which fault was on
```

## OpenAPI document

**What it does.** `demo_sut/openapi.json` is generated from the route table (paths,
parameters, request bodies, tags). A test fails when the committed file and the
routes drift, so a schema-diff gate has a reviewed baseline.

**How to use it.** `python -m demo_sut openapi` prints it;
`python -m demo_sut openapi --write` rewrites the committed file.

**Configuration.** None.

**Example.** `python -m demo_sut openapi | python -m json.tool | head`.

## Lifecycle definition

**What it does.** `demo_sut/config/subscription.machine.yaml` describes the
subscription lifecycle in a neutral workflow format: initial states, states with
transitions (action → target, optional condition), actions with titles and
operations, and the field schema. It has one deliberately unreachable state
(`suspended`) so a reachability analysis has a real finding. The demo serves it
at `GET /api/machines/subscription`, and the detail and draft screens render each
action as `data-action="<name>"`.

**How to use it.** Load it with any YAML reader; generators can treat it as their
example input.

**Configuration.** None.

**Example.** `curl -s localhost:8765/api/machines/subscription | python -m json.tool`.
