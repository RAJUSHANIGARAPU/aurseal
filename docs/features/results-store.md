# Results store

One SQLite database holds every run, invocation, test result attempt, evidence link,
verdict row and data-origin row, so trends, release decisions, traceability and the
dashboard all ask the same joined data instead of re-parsing report files. The code lives
in `results/`.

## Store and schema

**What it does.** `results.store.open_store()` returns a `ResultsStore`: a narrow protocol
(`begin_run`, `begin_invocation`, `upsert_cases`, `add_results`, `add_evidence`,
`ingest_verdicts`, `finish_run`, `query`, `close`) implemented by `SqliteResultsStore`
(stdlib `sqlite3`, WAL, `busy_timeout=30000`, foreign keys on). Reads go through *named*
queries in `results/queries/*.sql`; there is no ad-hoc SQL in the public interface.

Core tables (migration `core/0001_init.sql`), every one carrying `project_id`
(`'default'` for a single-project install):

| Table | Natural key | Holds |
|---|---|---|
| `projects` | `id` | tenants |
| `runs` | `run_id` | one execution: source, lane, environment, branch, commit, CI link, trigger, schedule, release label, expected count, status, integrity verdict, provenance |
| `invocations` | `(run_id, seq)` | each pytest invocation of a run (a `--lf` rerun is invocation 2) |
| `cases` / `case_revisions` | `(project_id, case_id)` / `(project_id, case_id, revision_id)` | the test-case catalogue and its revisions |
| `test_results` | `(run_id, nodeid, attempt)` | every attempt: outcome, phase durations, worker, browser, shard, message, longrepr, failure fingerprint, skip reason, defect ref |
| `final_results` (view) | — | the last attempt of each test, the run-trust gate's rule |
| `evidence` | `(run_id, nodeid, attempt, kind, uri)` | links (never blobs) to traces, videos, screenshots, failure bundles, reports |
| `verdicts` | all columns | mirror of `verdicts.jsonl` |
| `data_origin` | all columns | mirror of the data-origin file (fresh vs reused data) |
| `case_links` | `(project_id, case_id, kind, ref)` | requirement, defect, feature, doc, review and `tms:<name>` links |

Outcomes are exactly the run gate's vocabulary: `passed`, `failed`, `error`, `xfailed`,
`skipped`, `not_run`. A free-string test id from another framework
(`com.example.LoginTest#ok`, `login > rejects bad password`) is stored in `nodeid`.

**Migrations.** Forward-only numbered files per domain, `results/migrations/<domain>/NNNN_<desc>.sql`,
tracked in `schema_migrations` with their SHA-256. `core` applies first, other domains
alphabetically; a first line `-- requires: core>=1` is checked before anything runs. An
applied file whose checksum changed makes the store refuse to open: add a new migration
instead. A non-core domain must be listed in `results/migrations/DOMAINS` with its table
prefix and may only create `<prefix>_*` tables (it references core tables by foreign key
and never alters them). A file may have one conditional variant `NNNN_<desc>.nofts.sql`,
used when SQLite lacks FTS5. `PRAGMA user_version` mirrors the core version.

**Limits.** SQLite on a network filesystem is unsafe; keep the file on local disk. Inside
pytest there is one writer (the controller). A non-pytest framework writing concurrently
goes through the REST handlers or imports bundles one at a time. `longrepr` is truncated to
16 KB and `message` to 2 KB; the full text lives in the failure bundle (an evidence link).

## Recorder (pytest plugin)

**What it does.** `results.pytest_plugin` records every test into the store:

- **Single writer.** Only the controller opens the database. xdist replays every worker's
  report to the controller, so workers never open a connection.
- **Attempts.** A pytest-rerunfailures retry closes the attempt; the next one is attempt
  n+1. A second invocation with the same `TEST_RUN_ID` adds invocation 2 to the same run
  and continues the attempt numbering.
- **Gaps are visible.** A selected test that never reported (crash, `-x`, interrupt) is
  stored as `not_run`.
- **Batched writes.** One transaction per 200 results or 5 seconds, plus one at session end.
- **Side channels.** At session end this run's `verdicts.jsonl` and data-origin rows are
  ingested (idempotently), and the run-trust verdict is stored on the run.
- **Failures are loud, never fatal.** A store failure never changes a test outcome. The
  terminal summary prints `results-store: DEGRADED (<reason>)` and
  `$RESULTS_DIR/store-status.json` records it. `RESULTS_STORE_REQUIRED=true` turns it into
  exit status 3 (`INTERNAL_ERROR`).

**How to use.** Registered from the root `conftest.py` (`register(config)` after the run
gate), or explicitly with `-p results.pytest_plugin`.

**Evidence.** From a test or fixture, on any worker:

```python
from results.evidence import attach


def test_checkout(page, request):
    ...
    attach(request, "screenshot", "reports/screenshots/checkout.png")
```

`attach` appends `("evidence", {...})` to `user_properties` (uri, sha256, size); the
controller stores the link. Kinds: `trace`, `video`, `screenshot`, `failure_bundle`, `har`,
`log`, `allure`, `report`, `other`.

## Run bundle

**What it does.** A run bundle is NDJSON with one typed record per line: `run`,
`invocation`, `case`, `revision`, `result`, `evidence`, `verdict`, `provenance`,
`run_end`, each with `schema_version: 1`. The contract is
`results/schemas/bundle-v1.schema.json` (JSON Schema 2020-12). Every run can export its
bundle; the database can always be rebuilt from bundles; importing a bundle twice adds
nothing. Any framework in any language pushes results by writing a bundle, without
touching SQL:

```json
{"type":"run","schema_version":1,"run_id":"js-1","source":"bundle","framework":"jest"}
{"type":"result","schema_version":1,"run_id":"js-1","nodeid":"login > rejects bad password","outcome":"passed","duration_ms":5}
{"type":"run_end","schema_version":1,"run_id":"js-1","status":"finished"}
```

A bundle that breaks the schema is rejected as a whole, with the offending line number;
nothing is written. Files ending in `.gz` are gzip-compressed.

One run id may span several processes, for example one CI job per lane sharing
`TEST_RUN_ID`. Each job's bundle carries its own invocation 1; on import an invocation is
recognised by its start time, so the second job's invocation becomes invocation 2 of the
same run (with its own `lane`), its attempts continue after the ones already stored, and
importing either bundle again adds nothing.

## REST handlers

`results/api.py` holds framework-agnostic handlers, `handler(store, params, body) -> dict`,
listed in `ROUTES` for the platform API to mount under `/api/v1`. Errors raise `ApiError`
with an HTTP status.

| Method | Path | Purpose |
|---|---|---|
| POST | `/runs` | open a run; `run_id` is client-supplied or generated |
| POST | `/runs/{run_id}/bundle` | push NDJSON bundle records (idempotent) |
| POST | `/imports/bundle` | push a bundle for a run that is not open yet |
| PATCH | `/runs/{run_id}` | finish a run (status, integrity verdict) |
| GET | `/runs`, `/runs/{run_id}`, `/runs/{run_id}/results?outcome=&attempts=all` | read |
| GET | `/runs/{run_id}/bundle` | export the bundle |
| GET | `/schemas/{name}` | the JSON Schemas, so external pushers can validate first |

## Command line

```bash
python -m results db migrate
python -m results db status                       # exit 1 on a checksum mismatch
python -m results ingest --run-id "$TEST_RUN_ID"  # re-ingest verdicts + data origin
python -m results export bundle --run-id "$TEST_RUN_ID" --out reports/results/
python -m results import bundle reports/results/bundle-<run>.ndjson
python -m results prune --keep-runs 500           # or --older-than 180d
```

`--db PATH` overrides the database for any command.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RESULTS_DB` | `$RESULTS_DIR/results.db` | database path (`sqlite:///path` also accepted) |
| `RESULTS_DIR` | `reports/results` | store directory and `store-status.json` |
| `RESULTS_STORE` | `on` | `off` disables the recorder |
| `RESULTS_STORE_REQUIRED` | `false` | a degraded store fails the session (exit 3) |
| `RESULTS_LANE` | `$RUN_TRUST_LANE` | lane name stored on the run |
| `RESULTS_ARTIFACT_ROOT` | current directory | evidence URIs are stored relative to it |
| `RELEASE_LABEL`, `SUT_VERSION` | unset | stored on the run |
| `TEST_RUN_ID` | resolved by `core.run_id` | the run key shared by every process |

## Example

```bash
TEST_RUN_ID=local-42 pytest tests/ -n auto
python -m results export bundle --run-id local-42 --out reports/results/
sqlite3 reports/results/results.db \
  "SELECT outcome, COUNT(*) FROM final_results WHERE run_id='local-42' GROUP BY outcome"
```
