# Platform REST API

A versioned JSON-over-HTTP API (`/api/v1`) over the results store, so any framework in any
language can push results and read back what the platform concluded. The consumer guide,
with every payload, is [`docs/integration_api.md`](../integration_api.md); this page is
about running and extending it. The code lives in `qaplatform/`.

## What it does

- **Push:** create a run (idempotent on the client's `external_id`), append results in
  batches (idempotent on `(test_id, attempt)`), upload JUnit XML, append verdict rows
  (JSON or the `verdicts.jsonl` file as is), attach evidence links, finish the run.
- **Judge:** `finish` computes the run-trust verdict **on the server** with the run
  gate's own rules (`gate/integrity.py`) over what was actually stored, plus a `SELECTION`
  rule against the declared `expected_count`. A client cannot claim green by leaving
  results out: a shortfall is `UNJUDGED`, an all-skipped run is `FAIL`, and a missing
  `expected_count` is named in `rules_absent` instead of being assumed.
- **Read:** run summaries, results (final or every attempt), evidence, verdict rows, the
  outcome history of one test, a requirement traceability matrix, and the release verdict.
- **Trigger:** a run request is handed to a dispatcher (CI `workflow_dispatch`); the run that
  answers it links back through `run_request_id`.

The contract is the JSON Schemas in `qaplatform/schemas/v1/` (draft 2020-12). The OpenAPI
document at `/api/v1/openapi.json` is generated from the route table
(`qaplatform/http/routes.py`) and those schemas, and tests assert that the mounted routes,
the route table and the OpenAPI paths are the same set, and that every example in the
integration guide validates against its schema.

## Layers

| Module | Role |
|---|---|
| `qaplatform/service.py` | `PlatformService`: every read and write, validation, run judgement. Framework-free. |
| `qaplatform/store.py` | `PlatformStore` (results store + the `qap_*` tables) and `ScopedStore` (bound to one project). |
| `qaplatform/http/routes.py` | the route table and the request pipeline (auth, body decoding, limits, idempotency, problem+json). Framework-free. |
| `qaplatform/http/app.py` | the FastAPI adapter: mounts the route table, nothing else. |
| `qaplatform/client.py` | `PlatformClient`, the Python client (retries 429/502/503/504). |
| `qaplatform/ingest/` | JUnit XML (through the one JUnit reader in `results/io/junit.py`) and verdict lines. |
| `qaplatform/dispatch.py` | `NullDispatcher`, `GitHubWorkflowDispatcher`. |
| `qaplatform/readers.py` | traceability (default reader over the store) and the release-verdict seam. |

The platform's tables live in their own migration domain (`qaplatform/migrations/`, prefix
`qap_`), applied when a `PlatformStore` opens; the core results tables already carry
`project_id`.

## How to use

```bash
pip install -r requirements.txt -r requirements-platform.txt
python -m qaplatform init                 # default project + one admin token, printed once
python -m qaplatform token create --role tester --label ci   # a CI token, printed once
python -m qaplatform serve                # http://127.0.0.1:8765/api/v1/health
```

From any CI, without Python: see the `curl` sequence in the integration guide. With Python:

```bash
export QAP_URL=http://127.0.0.1:8765 QAP_TOKEN=qap_... QAP_PROJECT=default
python -m qaplatform push-junit reports/junit.xml --name nightly --external-id "$RUN" --finish
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `QAP_DB` | `$RESULTS_DB`, else `reports/results/results.db` | the store the server opens |
| `RUN_TRUST_MIN_EXECUTED`, `RUN_TRUST_MAX_SKIP_RATIO`, `RUN_TRUST_MIN_VERDICTS`, `RUN_TRUST_MIN_ACQUIRED` | the run gate's defaults | thresholds of the server-side integrity verdict |
| `QAP_DISPATCHER` | unset (requests stay `queued`) | `github` enables `workflow_dispatch` |
| `QAP_GITHUB_REPOSITORY`, `QAP_GITHUB_TOKEN` | none | `owner/name`, and a token allowed to dispatch workflows (never logged) |
| `QAP_GITHUB_TARGETS` | none | `target=workflow.yml,...`: only listed targets can be triggered |
| `QAP_GITHUB_REF`, `QAP_GITHUB_API_URL` | `main`, the public API | branch to run on; API base for a self-hosted instance |
| `QAP_BUILD_SHA` | `$GITHUB_SHA`, else `unknown` | reported by `/version` |
| `QAP_URL`, `QAP_TOKEN`, `QAP_PROJECT`, `QAP_TIMEOUT_S` | none, none, `default`, 30 | client-side CLI commands |

`serve` binds `127.0.0.1:8765` unless `--host/--port` say otherwise. SQLite connections are
per serving thread; keep the database on a local disk.

## Dependencies

FastAPI, Starlette, Pydantic, uvicorn, anyio and (for the tests) httpx are pinned in
`requirements-platform.txt` and imported only inside `qaplatform/http/app.py`, inside
functions. `tests/qaplatform` (service, pipeline, OpenAPI, client, CLI) runs offline without
them; `tests/qaplatform_http` (the real app via the test client, a recorded `curl` sequence,
and uvicorn on a loopback socket) is not collected when they are missing and runs in the
`platform` CI job.

## Example

```python
from qaplatform.client import PlatformClient

client = PlatformClient("http://127.0.0.1:8765", token, "default")
run = client.create_run("nightly", external_id="ci-1042", expected_count=2)
client.push_results(
    run["run_id"],
    [
        {"test_id": "cart::adds_item", "outcome": "passed", "requirements": ["REQ-7"]},
        {"test_id": "cart::removes_item", "outcome": "failed", "message": "expected 0, got 1"},
    ],
)
print(client.finish_run(run["run_id"])["integrity"])  # {'verdict': 'PASS', ...}
```

## Honest limits

- The release verdict has no built-in reader: until one is configured the endpoint answers
  501 rather than a stand-in decision.
- Server-side integrity can only judge what the client declares; without `expected_count`
  the summary says the selection rule is absent.
- JUnit XML is lossy (no verdict kinds, no attempts); the JSON path is the full-fidelity one.
- No outbound webhooks and no rate limiting yet; the client already honours `Retry-After`.
