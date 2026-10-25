# Integrating a test framework with the platform

This page is for the author of a test framework, in Python or in any other language, who
wants to send results to the platform and read back what the platform concluded. It applies
to API version **v1**, schema version **1.0**.

There are three integration levels; pick the lowest one that gives you what you need.

| Level | You write | You get |
|---|---|---|
| 1. JUnit XML upload | one CI step (`curl` or the CLI) | runs, outcomes, history, requirement links via `<property>` |
| 2. Native JSON push | a small reporter/listener in your runner | level 1 + attempts, verdict rows, evidence links, a judged run |
| 3. Python package | `import qaplatform` (Python frameworks only) | level 2 without writing HTTP code |

Reading (verdicts, traceability, release verdict) and triggering runs are the same REST calls
at every level.

---

## 1. Concepts

| Term | Meaning |
|---|---|
| **project** | The tenant. Every data URL is `/api/v1/projects/{project}/...`. Single-project installs use `default`. |
| **run** | One execution of a suite. Created `open`, receives results, then finished. Identified by the server-issued `run_id`; your own id goes in `external_id` and is unique per project. |
| **result** | One test outcome inside a run. Natural key `(run_id, test_id, attempt)`. |
| **test_id** | Your stable identifier for a test, any string up to 512 characters: `tests/test_login.py::test_ok`, `com.example.LoginTest::ok`, `login rejects bad password`. Keep it stable across runs: history is keyed on it. |
| **outcome** | `passed`, `failed`, `error` (setup or teardown broke), `xfailed` (an expected-red test that reproduced), `skipped`, `not_run` (selected but never reported). |
| **verdict row** | A statement about *what a test proved*, beyond pass/fail. `kind` is `expected_red` (a known defect still `reproduces`, was `cleared`, was `held_empty`, ...) or `coverage` (a requirement was `proved`, only `recorded`, `held_empty`, or the test `abstained`). |
| **integrity verdict** | The platform's judgement of whether the run is evidence at all: `PASS`, `FAIL` or `UNJUDGED`. A run where everything was skipped is not `PASS`; a run that reported fewer results than it declared is not `PASS`. |
| **release verdict** | Whether a named release has enough proved evidence, across runs, to ship. |
| **source** | `automated` (default), `manual`, `exploratory`, `import`. |

Only `coverage` verdicts with `verdict: "proved"` count as proof. A green run that proved
nothing is reported as such.

---

## 2. Transport rules

- Base URL: `<server>/api/v1`. JSON bodies, UTF-8, `Content-Type: application/json`.
- **Auth:** `Authorization: Bearer qap_<token>`. Tokens are issued per project and role,
  shown once, and always expire (default 90 days); `GET /me` returns the token's role and
  `expires_at`. A CI pipeline should use its own token with role `tester` (push results,
  read), never a personal or admin token.
- **Request ids:** send `X-Request-Id` if you have one; it is echoed back and appears in
  error documents.
- **Idempotency:** creating a run with an `external_id` that already exists in the project
  returns the existing run (`200`, not `201`). Results are keyed on `(test_id, attempt)`:
  resending a batch is a no-op. The verdicts, evidence and run-request POSTs honour an
  `Idempotency-Key: <uuid>` header: a repeat within 24 hours returns the first response.
- **Limits:** at most 1000 results per batch, 10 MB per JSON body, 50 MB per JUnit upload.
- **Pagination:** list endpoints take `?limit=` (1 to 1000, default 100) and `?cursor=`;
  a response carries `next_cursor` while there are more pages.
- **Errors:** `application/problem+json` (RFC 9457), for example:
  ```json
  {"type": "https://example.test/problems/run-closed", "title": "Run is finished",
   "status": 409, "detail": "run r-01 was finished at 2026-01-05T10:12:00Z",
   "instance": "/api/v1/projects/demo/runs/r-01/results", "request_id": "req_7f3"}
  ```
  | Status | When |
  |---|---|
  | 400 | body fails its JSON Schema (`errors[]` lists JSON pointers) or a parameter is invalid |
  | 401 | missing, unknown, expired or revoked token |
  | 403 | the token's role lacks the permission (for example a `viewer` pushing results) |
  | 404 | unknown project, run or resource, and also any project the token cannot see |
  | 409 | appending to a finished run; an `Idempotency-Key` reused for a different request |
  | 413 | body over the limit |
  | 501 | the feature is not configured on this server (for example the release verdict) |
- **Retries:** retry 429, 502, 503, 504 and connection errors with exponential backoff; every
  write is idempotent as described, so retrying is safe. Never retry 400, 401, 403, 404 or 409.
- **Versioning:** within v1 changes are additive only. Ignore response fields you do not
  recognise. Request bodies are strict: an unknown field is rejected, not dropped, so a typo
  cannot silently lose data. A breaking change ships as `/api/v2`.
- **Contract files:** `GET /api/v1/openapi.json` (generate a client from it) and
  `GET /api/v1/schemas/{name}` (the JSON Schemas, draft 2020-12). The same files live in the
  repository under `qaplatform/schemas/v1/`.

---

## 3. REST interface

All paths are relative to `/api/v1`. `{p}` is the project key.

### Health and discovery (no token)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness: `{"status": "ok"}` |
| GET | `/ready` | store reachable and migrated: `{"status": "ready", "schema_version": "1.0"}`, or 503 |
| GET | `/version` | `{"api": "v1", "schema_version": "1.0", "build": "<commit or unknown>"}` |
| GET | `/openapi.json`, `/schemas/{name}` | the contract |
| GET | `/me` (token) | the calling token: user, role, project, expiry |

### Projects

| Method | Path | Purpose |
|---|---|---|
| GET | `/projects` | projects this token can see (a project token sees only its own) |
| GET | `/projects/{p}` | one project: key, name, archived, your role |
| POST | `/projects` | create a project (instance admin; the creator becomes its admin) |
| PATCH | `/projects/{p}` | rename, change settings, archive or unarchive (admin) |

An archived project is read-only: every write answers 409 until it is unarchived.
Run ids, test ids, requirement and ticket keys are unique *within* a project; two projects
may use the same `external_id` or `test_id` without seeing each other.

### Push results (role `tester` or higher)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{p}/runs` | create a run, or fetch the one with this `external_id` |
| POST | `/projects/{p}/runs/{run_id}/results` | append a batch of results |
| POST | `/projects/{p}/runs/{run_id}/junit` | append results from JUnit XML (`Content-Type: application/xml`) |
| POST | `/projects/{p}/runs/{run_id}/verdicts` | append verdict rows (JSON array, or JSONL with `application/x-ndjson`) |
| POST | `/projects/{p}/runs/{run_id}/evidence` | attach links to reports, traces, logs (also after finish) |
| POST | `/projects/{p}/runs/{run_id}/finish` | close the run; computes the integrity verdict |

### Manual execution (role `tester` or higher)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{p}/manual-runs` | start a manual run from spec markdown (`spec`) or a case list (`cases`) |
| GET | `/projects/{p}/manual-runs/{run_id}` | executions, their steps and what is still missing |
| PUT | `/projects/{p}/manual-executions/{id}/steps/{n}` | record a step: `pass`, `fail`, `blocked` or `na` |
| POST | `/projects/{p}/manual-executions/{id}/finish` | turn the steps into one result |

A tool that already runs manual tests can instead push its outcomes as a normal run with
`source: "manual"` and a `steps` array per result.

### Exploratory sessions (role `tester` or higher)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{p}/sessions` | start a session: `charter`, `area`, `requirements`, `timebox_min` |
| GET | `/projects/{p}/sessions?requirement=` | sessions, newest first |
| GET | `/projects/{p}/sessions/{id}` | charter, notes, bugs, debrief, duration and timebox |
| POST | `/projects/{p}/sessions/{id}/notes` | `observation`, `question`, `bug` or `idea`, with optional `attachment_uri` and `defect_key` |
| POST | `/projects/{p}/sessions/{id}/end` | `debrief`; `abandoned: true` records nothing |

An ended session counts as `recorded` for its requirements, never as `proved`.

### Read (any role)

| Method | Path | Purpose |
|---|---|---|
| GET | `/projects/{p}/runs?status=&branch=&source=&since=` | list runs, newest first |
| GET | `/projects/{p}/runs/{run_id}` | run summary, including the integrity verdict |
| GET | `/projects/{p}/runs/{run_id}/results?outcome=&attempts=all` | results (final attempts unless `attempts=all`) |
| GET | `/projects/{p}/runs/{run_id}/evidence` | evidence links |
| GET | `/projects/{p}/verdicts?run_id=&ticket=&kind=` | verdict rows |
| GET | `/projects/{p}/history?test_id=` | outcome history of one test across runs |
| GET | `/projects/{p}/traceability?requirement=&release=` | requirement - test - latest verdict matrix |
| GET | `/projects/{p}/requirements/{req}` | status of one requirement |
| GET | `/projects/{p}/releases/{release}/verdict` | release verdict (501 when no release policy is configured) |

### Trigger runs (role `lead` or higher)

| Method | Path | Purpose |
|---|---|---|
| POST | `/projects/{p}/run-requests` | ask for a run; returns `202` and the request |
| GET | `/projects/{p}/run-requests/{id}` | poll: `queued`, `dispatched`, `running`, `finished`, `failed` or `rejected`; `run_id` once known |

The platform does not execute tests itself. It hands the request to the configured
dispatcher (for example a CI `workflow_dispatch`), which receives the request id as the
`run_request_id` input. The triggered job pushes its results like any other run and passes
`run_request_id` when it creates the run: that link moves the request to `running`, and
finishing the run moves it to `finished`. With no dispatcher configured a request stays
`queued` and its `detail` says so.

---

## 4. Payloads

The schemas in `qaplatform/schemas/v1/` are authoritative; this section shows the shapes.
Every example below is validated against its schema by the test suite.

### Create a run (`run-create`)

<!-- validates: run-create -->
```json
{"schema_version": "1.0", "name": "nightly", "external_id": "ci-1042",
 "source": "automated", "lane": "api", "environment": "staging", "release": "2.4.0",
 "expected_count": 40,
 "provenance": {"repository": "example/shop", "branch": "main", "commit": "a1b2c3d",
                "framework": "junit5", "framework_version": "5.10"},
 "labels": {"team": "checkout"}}
```

`expected_count` is the number of tests the run *selected*. Send it whenever your runner
knows it (at creation or at finish): without it the platform cannot tell "all 40 ran" from
"8 of 40 ran", and the integrity block says so in `rules_absent`. With it, a run that
reports fewer results than declared is `UNJUDGED`, never `PASS`.

### Append results (`results-batch`, items are `test-result`)

<!-- validates: results-batch -->
```json
{"schema_version": "1.0", "results": [
  {"test_id": "billing.InvoiceTest::totalsIncludeTax", "outcome": "passed", "duration_ms": 412,
   "requirements": ["REQ-12"]},
  {"test_id": "billing.InvoiceTest::refundRounding", "outcome": "failed", "attempt": 1,
   "message": "expected 10.00 but was 9.99", "tickets": ["BUG-142"],
   "evidence": [{"kind": "trace", "uri": "artifacts/refundRounding.zip"}]},
  {"test_id": "billing.InvoiceTest::refundRounding", "outcome": "passed", "attempt": 2}
]}
```

Retries: send each attempt as its own result with `attempt` 1, 2, .... The platform keeps
every attempt; the summary reports the final attempt and counts a test that needed a retry
to pass as `flaky`. A retry does not erase a flake.

Manual or tool-driven results may carry `steps`
(`{"no": 1, "status": "pass|fail|blocked|na", "title": "...", "actual": "...", "evidence_uri": "..."}`).

### Verdict rows (`verdicts-batch`, items are `verdict-row`)

<!-- validates: verdicts-batch -->
```json
[{"test_id": "billing.InvoiceTest::refundRounding", "kind": "expected_red", "ticket": "BUG-142",
  "verdict": "reproduces", "detail": "rounding still truncates"},
 {"test_id": "billing.InvoiceTest::totalsIncludeTax", "kind": "coverage", "subject": "REQ-12",
  "verdict": "proved", "detail": "total=121.00 for net=100.00 at 21%"}]
```

`nodeid` is accepted as an alias of `test_id` and unknown fields are ignored, so a
`verdicts.jsonl` file written by the Python package can be uploaded unchanged with
`Content-Type: application/x-ndjson`. A JSONL upload is tolerant (bad lines are counted in
`dropped`); a JSON array is strict (one bad row rejects the request).

### Evidence links (`evidence-batch`)

<!-- validates: evidence-batch -->
```json
{"schema_version": "1.0", "links": [
  {"kind": "report", "uri": "https://ci.example.test/run/1042/report/"},
  {"kind": "trace", "uri": "artifacts/refundRounding.zip", "test_id": "billing.InvoiceTest::refundRounding",
   "attempt": 1, "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"}]}
```

Kinds: `trace`, `video`, `screenshot`, `failure_bundle`, `har`, `log`, `allure`, `report`,
`other`. The platform stores links, never files: keep artifacts in your CI's artifact store.

### Finish a run (`run-finish`)

<!-- validates: run-finish -->
```json
{"schema_version": "1.0", "expected_count": 40, "interrupted": false}
```

The response is the run summary. Finishing a finished run returns it unchanged.

### Run summary (`run-summary`, response)

<!-- validates: run-summary -->
```json
{"schema_version": "1.0", "run_id": "r-5d1c", "external_id": "ci-1042", "project": "demo",
 "name": "nightly", "status": "finished", "source": "automated", "release": "2.4.0",
 "lane": "api", "environment": "staging", "run_request_id": null,
 "tally": {"selected": 40, "received": 40, "passed": 37, "failed": 1, "error": 0,
           "xfailed": 1, "skipped": 1, "not_run": 0, "flaky": 2},
 "verdicts": {"coverage": {"proved": 12, "recorded": 3, "held_empty": 0, "abstained": 1},
              "expected_red": {"reproduces": 1, "cleared": 0, "held_empty": 0,
                               "signature_mismatch": 0, "closed_pass": 0,
                               "closed_regressed": 0}},
 "integrity": {"verdict": "PASS", "assessed": true, "breaches": [],
               "rules_absent": ["fixture provenance - no test needing acquired data was selected"]},
 "started_at": "2026-01-05T10:00:00.000Z", "finished_at": "2026-01-05T10:12:00.000Z",
 "links": {"self": "/api/v1/projects/demo/runs/r-5d1c"}}
```

The integrity rules are the run gate's: an executed floor, a skip ratio, detector verdicts
(an expected-red test that was selected must reach a verdict) and fixture provenance, plus
the platform's own `SELECTION` rule against `expected_count`. A rule that does not apply is
listed in `rules_absent` rather than counted as satisfied. Thresholds are the server's
`RUN_TRUST_*` settings.

### Traceability (`traceability`, response)

<!-- validates: traceability -->
```json
{"schema_version": "1.0", "project": "demo", "release": null,
 "generated_at": "2026-01-05T10:15:00.000Z",
 "rows": [{"requirement": "REQ-12", "status": "proved", "open_tickets": [],
           "tests": [{"test_id": "billing.InvoiceTest::totalsIncludeTax",
                      "latest_outcome": "passed", "latest_verdict": "proved",
                      "run_id": "r-5d1c", "source": "automated",
                      "at": "2026-01-05T10:00:00.000Z"}]}]}
```

Status per requirement, strongest first: `failing` (a linked test's latest outcome is
failed or error), `proved` (a `coverage` verdict `proved` from a passing test), `recorded_only`
(tests passed or verdicts were only `recorded`), `held_empty`, `no_tests`.

### Release verdict (`release-verdict`, response)

<!-- validates: release-verdict -->
```json
{"project": "demo", "release": "2.4.0", "decision": "no_go",
 "reasons": [{"rule": "requirements proved", "result": "breached", "detail": "11 of 12"}],
 "evaluated_at": "2026-01-05T10:20:00.000Z"}
```

`not_assessed` means the platform lacked the evidence to decide. Treat it as "do not ship".
The server decides from `release-policy.yaml` (or the file `QAP_RELEASE_POLICY` names),
optional waivers (`release-waivers.yaml` or `QAP_RELEASE_WAIVERS`), the project's runs
labelled with that release (only run-trust PASS runs count) and the last defect snapshot.
`go` covers GO and GO_WITH_WAIVERS; `evidence.verdict` keeps the exact one. Each reason's
`rule` is the policy criterion: `breached` blocks, `absent` means no evidence either way.
A server without a release policy answers 501 rather than guessing.

### Run requests (`run-request`, response `run-request-status`)

<!-- validates: run-request -->
```json
{"schema_version": "1.0", "target": "regression", "environment": "staging",
 "params": {"shard_count": 4, "headed": false}}
```

---

## 5. Level 1: JUnit XML (any language, no code)

Most runners in every mainstream language emit JUnit XML. Upload it after the test step:

```bash
RUN=$(curl -sf -X POST "$QAP_URL/api/v1/projects/$QAP_PROJECT/runs" \
  -H "Authorization: Bearer $QAP_TOKEN" -H "Content-Type: application/json" \
  -d "{\"schema_version\":\"1.0\",\"name\":\"nightly\",\"external_id\":\"$CI_RUN_ID\",
       \"provenance\":{\"commit\":\"$GIT_SHA\",\"branch\":\"$GIT_BRANCH\"}}" | jq -r .run_id)

for f in build/test-results/*.xml; do
  curl -sf -X POST "$QAP_URL/api/v1/projects/$QAP_PROJECT/runs/$RUN/junit" \
    -H "Authorization: Bearer $QAP_TOKEN" -H "Content-Type: application/xml" --data-binary @"$f"
done

curl -sf -X POST "$QAP_URL/api/v1/projects/$QAP_PROJECT/runs/$RUN/finish" \
  -H "Authorization: Bearer $QAP_TOKEN" -H "Content-Type: application/json" \
  -d '{"schema_version":"1.0"}' | jq .integrity
```

Mapping: `<testcase classname="A" name="b">` becomes `test_id = "A::b"` (pytest's XML maps
back to its node ids); `<failure>` is `failed`, `<error>` is `error`, `<skipped>` is
`skipped`, none is `passed`. Requirement links:
`<properties><property name="requirement" value="REQ-12"/></properties>` on the test case
(repeatable); `defect` / `known_defect` properties become tickets; `[[ATTACHMENT|path]]` in
the output becomes an evidence link. A document with a `DOCTYPE` or `ENTITY` declaration is
refused. Not available through JUnit: verdict rows and attempts; use level 2 for those.

Run the upload in an **always-run** CI step, so a red test step still uploads. Do not let an
upload failure turn a green build red unless you want the platform to be a gate.

## 6. Level 2: native JSON reporter (any language)

Write a listener or reporter in your runner that:

1. On session start: `POST /runs` with `external_id`, `provenance` and, once collection is
   done, `expected_count` (or send it with `finish`).
2. Buffers results and flushes `POST /runs/{id}/results` every few hundred results or few
   seconds, and at the end (at most 1000 per request).
3. Sends verdict rows (`POST /runs/{id}/verdicts`) if your framework distinguishes "proved"
   from "passed"; this is what lifts a run from "green" to "evidence".
4. On session end, also on interrupt: `POST /runs/{id}/finish`, with `interrupted: true` when
   the session did not complete.
5. Never fails the test run because the platform is unreachable: log, keep a local JSON copy
   of the unsent batches, and replay them later (writes are idempotent).

## 7. Level 3: Python package

```python
from qaplatform.client import PlatformClient

client = PlatformClient(base_url, token, project="demo")  # requests-based, timeout 30 s
run = client.create_run(
    "nightly",
    external_id=ci_run_id,
    expected_count=40,
    provenance={"commit": sha, "branch": branch},
)
client.push_results(run["run_id"], results)  # list of test-result dicts; batched
client.push_junit(run["run_id"], "reports/junit.xml")
client.push_verdicts(run["run_id"], "reports/verdicts/verdicts.jsonl")
summary = client.finish_run(run["run_id"])  # summary["integrity"]["verdict"]
for row in client.verdicts(run_id=run["run_id"], ticket="BUG-142"):  # follows cursors
    ...
client.traceability(requirement="REQ-12")
client.release_verdict("2.4.0")
req = client.request_run("regression", environment="staging")
client.wait_for_run_request(req["id"], timeout_s=3600)
client.health()  # never raises on 503
```

Errors raise `qaplatform.errors` classes mapped from the problem document: `BadRequest`
(with `.errors`), `Unauthorized`, `Forbidden`, `NotFound`, `RunClosed`, `Conflict`,
`TooLarge`, `NotConfigured`. `qaplatform.schemas.validate(name, payload)` validates a payload
offline before sending.

CLI, useful from any language's CI:

```
python -m qaplatform push-junit reports/*.xml --name nightly --external-id "$CI_RUN_ID" --finish
python -m qaplatform push-results results.json --run <run_id>
python -m qaplatform push-verdicts reports/verdicts/verdicts.jsonl --run <run_id>
python -m qaplatform validate test-result payload.json
python -m qaplatform health
```

Environment: `QAP_URL`, `QAP_TOKEN`, `QAP_PROJECT` (default `default`), `QAP_TIMEOUT_S`.

## 8. Using the platform as a CI gate

`GET /projects/{p}/runs/{run_id}` after finish and fail unless `integrity.verdict == "PASS"`;
or, once a release policy is configured, `GET /projects/{p}/releases/{release}/verdict`
and fail unless `decision == "go"`. Fail on `not_assessed` and `UNJUDGED` too: absence
of evidence is not a pass.

## 9. Security notes for integrators

- Store the token in your CI's secret store, never in the repository or a job log. It is
  shown once at creation; only its hash is stored.
- Use one `tester` token per pipeline, so a leaked token can only push results into one
  project and is attributable.
- Results and messages may contain data from the system under test. Do not push secrets or
  personal data in `message`, `trace` or `detail`; the platform stores them as given.
- The server binds to loopback by default. Exposing it to a hosted CI means TLS in front of
  it and token rotation; if it is not reachable, write the JSON payloads to an artifact and
  replay them later.

## 10. Compatibility checklist for a new integration

- [ ] `test_id` is stable across runs and unique within a run (per attempt).
- [ ] `expected_count` is sent, or you accept the `selection` rule being absent.
- [ ] The finish call runs on interrupt as well as on success.
- [ ] Retries are recorded as attempts, not overwritten.
- [ ] Upload failures do not change the test job's own result unless chosen deliberately.
- [ ] A payload sample validates (`python -m qaplatform validate <schema> <file>`, or any
      JSON Schema 2020-12 validator in your language).
