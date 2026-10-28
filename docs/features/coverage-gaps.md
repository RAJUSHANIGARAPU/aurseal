# Coverage gaps

A deterministic report of what is specified but not tested: API operations from an
OpenAPI document, scenarios from `specs/*.md`, requests from a Postman collection and
active requirements from the registry. No model calls. The code lives in
`traceability/gaps.py` and `traceability/endpoint_hits.py`. The `/detect-coverage-gaps`
slash command has no parser of its own: `tools/slash/coverage_gaps.py` runs the same two
ratchets as CI (this one and the workflow-action one below) with `--report-only`, and a
test checks its report equals the CI report for a fixture.

## Sources and evidence

**What it does.** Every source item gets one status:

| Status | Meaning |
|---|---|
| `covered` | a test tags it (`covers` marker) or links a requirement it names |
| `covered_not_passing` | covered, but none of its tests passed in the given run |
| `exercised` | a run called the operation (runtime hits), no test claims it: a call, not an assertion |
| `gap` | nothing |

| Source | Item key | Covered by |
|---|---|---|
| `--openapi file.json` or `--openapi https://...` | `GET /api/customers/{id}` | `@pytest.mark.covers("GET /api/customers/{id}")` (parameter names do not matter) |
| `--specs specs/` | `subscription#cancel-refunds` per `## Scenario` | `covers("spec:subscription#cancel-refunds")`, or a requirement id in the scenario's `### Tags` that a test links |
| `--postman c.json` (or a URL) | `POST /orders` | `covers("POST /orders")` |
| `--requirements` | `REQ-4` | a test linked to REQ-4 |

Matching is by explicit tag or requirement link only: fuzzy title matching is not done,
because a false "covered" is worse than a visible gap. A source that lists nothing (or
cannot be read) is an error, never "zero gaps".

```python
@pytest.mark.covers("GET /api/customers/{id}", "spec:subscription#cancel-refunds")
def test_cancel_refunds_the_unused_days(demo_http): ...
```

## Runtime hits

With `ENDPOINT_HITS=true` (or `ENDPOINT_HITS_FILE=path`), every `HttpEngine` appends
`{method, path, status, nodeid}` per response to `reports/traceability/endpoint-hits.jsonl`
(xdist-safe appends). `--hits` feeds them to the report, which then shows `exercised`
items. Hits prove a call happened, never that the behaviour was asserted.

## The ratchet

`traceability/gap-baseline.json` lists the accepted gaps (`source:key`). With
`--baseline`:

- a gap **not** in the baseline fails the step (exit 1) and is printed as `NEW GAP`;
- a baselined gap that closed also fails, as `stale baseline entry, delete it: <id>`, so
  the baseline only shrinks (`--allow-stale` turns this back into a reminder);
- the baseline changes only in a reviewed pull request (`--write-baseline` rewrites it).

`gaps.json` carries `ratchet: {new, closed, passed}`; the release verdict reads it.

So the report compounds without a "fix every gap first" blocker.

**Report-only views** (the ratchet never reads them): `--groups FILE` maps a group (a
deployment target) to source-item prefixes or `tag:<requirement>` and adds a "Gaps per
group" table with an `(ungrouped)` row; `--frozen-ref REF` re-reads the same file sources
at a git ref (a release tag), gives its gap count and the ids present on only one side, or
"not measured" when the ref lacks a source file.

## How to use

```bash
# The CI step (static mode, blocking)
python -m traceability gaps --openapi demo_sut/openapi.json --specs specs --requirements \
    --baseline traceability/gap-baseline.json

# Runtime mode after a run, report only
ENDPOINT_HITS=true pytest tests/demo_examples -m "not demo_ui" -n auto
python -m traceability gaps --openapi demo_sut/openapi.json \
    --hits reports/traceability/endpoint-hits.jsonl --reports reports/ --report-only
```

Writes `reports/traceability/gaps.json` (schema `traceability/schemas/gap-report.schema.json`)
and `gaps.md`. `--db PATH --run-id ID --branch B` also stores the report.

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/coverage-gaps/latest?branch=` | newest stored report |
| GET | `/api/v1/runs/{run_id}/coverage-gaps` | the report stored for a run |

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `ENDPOINT_HITS` | off | record runtime hits |
| `ENDPOINT_HITS_FILE` | `reports/traceability/endpoint-hits.jsonl` | hits file (setting it also enables recording) |
| `--baseline` | — | accepted gaps file; without it the report never fails |
| `--report-only` | off | never fail on new gaps |

## Workflow actions

`python -m traceability workflow-gaps` (`traceability/workflow_gaps.py`) lists every
`(state, action)` pair of the workflow definitions in a config dir (`--config-dir`, else
`$EXT_CONFIG_PATH`). A pair is named when its action is a string literal, the whole
literal, in a test module (`test_*.py` / `*_test.py` under `--tests`, default `tests`,
`ui/tests`, `api/tests`). The ratchet over `traceability/workflow-gap-baseline.json`
fails on a **new** gap and on a **stale** baseline entry (no longer a gap) until it is
removed. A config dir that is not bound (not given, missing, without a workflow, or not
parsing) is **NOT-MEASURED**: the header reads `GAPS=NOT-MEASURED ...`, never 0, and the
exit code is 3.

```text
GAPS=0 ACTIONS=16 NEW=0 STALE=0
```

Writes `workflow-gaps.md` and `workflow-gaps.json` (schema
`traceability/schemas/workflow-gap-report.schema.json`) under `--out`. Exit 0 clean, 1 new
or stale, 2 bad input (an unreadable baseline), 3 not measured; `--report-only` always 0.
`--write-baseline` rewrites the baseline from the current gaps.

## Honest limits

Runtime hits show an endpoint was exercised, not that a test checked its answer. Spec
coverage is only as good as the tags: a scenario nobody tagged stays a gap even if some
test happens to walk it.
A string literal names a workflow action, nothing more: it may sit in an unrelated test.
