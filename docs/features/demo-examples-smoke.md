# Demo examples: release smoke and read-only checks

Examples under `tests/demo_examples/smoke/` that run release-smoke style checks against the
demo service (see [demo-sut.md](demo-sut.md) and [demo-examples.md](demo-examples.md)). They
are API-level and run in the offline suite. `read_only` (in the area's `conftest.py`) is a
`gate.read_only.ReadOnlyClient` on the demo's base URL, which has no write verbs.

```bash
pytest tests/demo_examples/smoke -n auto
```

## Gateway route reaches its service

**What it shows.** `test_gateway_routes.py::test_new_route_reaches_service_not_gateway_404` is
parametrised over the gateway's forwarded areas (`jobs`, `documents`, `invoices`, `messages`,
`customers`, `subscriptions`). For each one it asks for an unknown id through `/gw/<area>/`
and requires the service's not-found body. A paired control path (`/gw/<area>x/...`) that the
route predicate must reject has to come back as the gateway's own no-route body. The bodies
are told apart by `api.clients.route_probe.classify_not_found` with the demo's shapes, and
`require_measured` makes sure a response actually got routed before it is judged.

**Configuration.** The body shapes are a `NotFoundShapes`:

```python
NotFoundShapes(
    gateway_keys=frozenset({"source", "path"}),
    error_key="error",
    error_text="no_route",
    service_keys=frozenset({"resource", "id"}),
)
```

## Write route reaches its service without writing

`test_write_route_probe.py` sends `POST /gw/tasks` with an empty body through
`api.clients.route_probe.probe_write`: the task service must refuse it with its
own 422 naming `title`, a path outside the route predicate must get the
gateway's 404, and a read on the route must answer 200. The task count is the
same before and after. The second test sends a valid title and shows that an
accepted probe is reported as `SIDE_EFFECT`, never as a pass.

## Fix delta judged per environment

**What it shows.** `test_fix_scope_delta.py` starts a second demo instance. The two instances
stand for two environments: `fixed-demo` (no fault) and `unfixed-demo` (fault
`reseller_share_scaled_twice` on, so a share of 6 is stored as 0.06). On each one the test
creates reseller terms with a 6 % share, then judges them through
`verification.fix_scope.judge` with one read-only oracle ("the share percent is kept"). The
control requires the flat fee to read back as sent, so the read reaches the right record. The
fixed environment is asserted and its pass is recorded as release evidence (verdict
`cleared`). On the other environment the oracle runs as an expected-red detector (verdict
`reproduces`). The failure message and the user properties carry each instance's fault set.

**Configuration.** `DEMO_RESELLER_FIX_ENVS` lists the environments where the fix is expected
(default `fixed-demo`). The test clears the variable so its outcome is fixed.

## Integrations: optional parameters and base-URL validation

**What it shows.** `test_integrations_api.py::test_optional_params_listed_and_base_url_validated`
checks that every service in `GET /api/integrations/services` lists `optionalParams`, and that
`baseUrl` is optional rather than required. Registering with a base URL that is plain http,
loopback (`localhost`, `127.0.0.1`, `[::1]`), private (`10.0.0.1`), link-local metadata
(`169.254.169.254`) or relative is refused with 400 and `field: baseUrl`, and the stored
integrations stay unchanged. A public https URL is accepted. A registration without the
required parameters is refused too.

## Platform smoke: jobs, files, exports

**What it shows.** `test_platform_smoke.py::test_jobs_files_exports_read_back` seeds and books a
subscription, which creates jobs and documents. It then runs `oracles.smoke.run_smoke` over a
`GetOnlyApi`: the jobs page is non-empty, the file list is non-empty, and the export list is
an array. One job and one file are read back by id. Finally it creates an export and reads it
back: every export record carries `attemptCount` and `lastAttemptAt`, and the new one has
`attemptCount >= 1` and `lastAttemptAt` not before `created_at`.

## Sequence numbers issued by the service

**What it shows.** `test_id_sequences.py::test_issued_numbers_well_formed_monotonic` starts
with a control: a client that sends its own number gets a service-issued one instead. It then
creates customers (individual and company) and partners, and checks the numbers with
`oracles.id_sequences.check_sequence` (`TYPE-UNIT-n`, unpadded counter): well formed,
strictly increasing per type, none shared, and the same on read-back. All numbers come from
one unit.

## Role-history events name their roles

**What it shows.** `test_role_history.py` reads every user's role history over a time window
(read-only) and requires each `ASSIGNED`/`REVOKED` event to list its roles. The demo clock is
frozen on a different day per test, so each window holds only that test's events.

* `test_role_events_name_roles`: assigns and revokes a role, then judges the window.
* `test_no_role_event_in_the_window_abstains`: an empty window skips: no events is not
  evidence.
* `test_role_history_is_chronological_detector[fault-on/off]`: an expected-red pair
  (`DEMO-113`, fault `role_history_sorted_as_text`) on a private demo whose event ids start
  low. Five assign/revoke pairs give at least eleven events crossing from id 9 to id 10, where a text sort and a number sort
  disagree; that crossing is the positive control. With the fault on the detector reports
  `reproduces`; with it off it reports `KNOWN DEFECT CLEARED`, and the test asserts that.
