# Manual test execution

A thin built-in runner for manual tests whose results land in the same store, next to the
automated ones, so traceability and the run-trust verdict see both. It is deliberately
small: a step-by-step form, not a test-management product. The rules are in
`qaplatform/manual.py`; storage in `qap_manual_executions` and `qap_manual_step_results`
(migration `0004_manual.sql`).

## What it does

- **Cases are spec files.** A manual case is the repository's spec format: a
  `## Scenario <name>` section with numbered `### Steps`, `### Expected` bullets,
  `### Preconditions`, and requirement keys (`REQ-12`) under `### Tags`. The same spec can be
  executed by hand or generated into an automated test. Cases can also be sent as JSON.
- **A manual run** is a run with `source: manual` and `expected_count` = number of cases, so
  a case nobody finished keeps the run `UNJUDGED`, never `PASS`.
- **Each execution pins the case revision** it followed (a hash of the steps and expected
  outcomes). Editing the spec later creates a new revision; history is not rewritten.
- **Steps** are recorded as `pass`, `fail`, `blocked` or `na`, with what actually happened
  and an optional evidence link, and can be corrected until the case is finished. Finishing
  needs every step recorded and maps them to one result:

  | Steps | Result |
  |---|---|
  | any `fail` | `failed`, message "step N failed: <actual>" |
  | else any `blocked` | `skipped` with reason "blocked at step N" (blocked work was **not executed**) |
  | all `na` | `skipped` |
  | else | `passed` |

- **Proof is graded.** A manual pass writes a `coverage` verdict per linked requirement:
  `proved` when every passing step carries an evidence link, otherwise `recorded`. Only
  `proved` counts as evidence in traceability, so a release can see whether it rests on an
  artifact or on a tester's word.
- **Audited** like every change (`manual_run.start`, `manual.step`, `manual.finish`); role
  `tester` or higher records, a `viewer` can only read.
- **External tools** can skip the runner: push results with `source: "manual"` and a `steps`
  array through the generic results endpoint (see `docs/integration_api.md`).

## How to use

In the UI: **Manual** → paste a spec, name the run, **Start** → open a case → record each
step → **Finish this case** → when done, **Finish the run** (opens the run page with its
verdict).

Over REST:

| Method | Path | Body |
|---|---|---|
| POST | `/api/v1/projects/{p}/manual-runs` | `{"schema_version": "1.0", "name": "release check", "spec": "<markdown>"}` or `"cases": [...]` |
| GET | `/api/v1/projects/{p}/manual-runs/{run_id}` | executions with steps, recorded results, missing steps |
| PUT | `/api/v1/projects/{p}/manual-executions/{id}/steps/{n}` | `{"schema_version": "1.0", "status": "fail", "actual": "error 500", "evidence_uri": "https://..."}` |
| POST | `/api/v1/projects/{p}/manual-executions/{id}/finish` | |
| POST | `/api/v1/projects/{p}/runs/{run_id}/finish` | close the run (the normal run finish) |

## Configuration

None. Evidence is a link (local path or URL) to a file kept elsewhere; the platform never
stores uploads.

## Example

```markdown
## Scenario Refund a cancelled order

### Steps
1. Cancel the order
2. Open the refunds page

### Expected
- The refund is listed

### Tags
REQ-13
```

Recording step 1 `pass` and step 2 `fail` ("refund missing") and finishing gives a
`failed` result `step 2 failed: refund missing`, and `REQ-13` shows as `failing` in the
traceability matrix.

## Honest limits

No file uploads (links only), no assignment or scheduling of manual work, no shared
editing of a case while it runs. If a dedicated test-management tool is adopted, use its
export and the generic results endpoint instead of this runner.
