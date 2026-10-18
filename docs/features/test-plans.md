# Test plans

A plan is a reviewed YAML file that names a scope (which tests) and exit criteria (what
must hold before the scope counts as done). It lives in the repository, so the scope is
versioned with the code and changed by pull request. The code is in `traceability/plans.py`
and `traceability/plan_plugin.py`.

Plans stop at scope and criteria on purpose: no assignees, no progress tracking, no manual
steps. A manual-execution feature would extend plans rather than this page faking it.

## Writing a plan

**What it does.** `plans/<plan-id>.yaml`, validated by
`traceability/schemas/plan.schema.json`. The `id` must equal the file name; the plan's
*revision* is the git blob hash of the file, recorded with every evaluation.

```yaml
id: release-1-2
title: Release 1.2 regression
release: "1.2"
environments: [qa]
scope:
  include:                      # union of the selectors
    - marker: "smoke or api"    # pytest -m grammar
    - requirements: [REQ-1, REQ-2]
    - requirement_labels: [billing]
    - nodeids: ["tests/demo_examples/finance/", "tests/api/test_*.py::*"]
  exclude:
    - marker: slow
sections:                       # optional, owners are free text
  - {name: billing, owner: "@billing", requirements: [REQ-4]}
exit_criteria:
  run_integrity: PASS
  min_pass_rate: 0.95
  no_failures_in_requirements: all
  requirements_proved: [REQ-4]
  max_open_defects: {any: 0}    # or per severity label: {high: 0, low: 5}
  detectors_reached_verdict: true
  no_draft_evidence: true
```

A `nodeids` entry without a wildcard matches that node id, or everything under that path
or file. **Every selector must match at least one test**: a typo is an error, never a
quietly smaller plan.

## Running a plan

```bash
python -m traceability plan lint plans/           # validate + every selector matches
python -m traceability plan resolve plans/x.yaml  # the exact node ids it selects
pytest --plan plans/x.yaml -n auto                # run exactly those tests
```

`--plan` deselects everything outside the plan (shown as deselected), so the normal
runner, xdist, the run-trust gate and the results recorder apply unchanged. A plan
that selects nothing leaves pytest with exit status 5; never mask it in CI. Load the
option with `-p traceability.plan_plugin` until the root conftest lists the plugin.

## Evaluating a plan

```bash
python -m traceability plan evaluate plans/x.yaml --reports reports/
python -m traceability plan evaluate plans/x.yaml --db reports/results/results.db \
    --runs 1234-1 1234-2 --save
```

Each criterion reports `met`, `unmet` or `no_evidence`, and **`no_evidence` never counts
as met**. Exit codes: 0 met, 1 unmet, 3 no evidence. Writes `plan-<id>.json` and `.md` to
`reports/traceability/`.

| Criterion | met | unmet | no_evidence |
|---|---|---|---|
| `run_integrity: PASS` | every run in scope PASS | a run FAIL / UNJUDGED / no marker | no run |
| `min_pass_rate` | passed / (passed+failed+error) at or above the bar | below | nothing executed |
| `no_failures_in_requirements` | rows PASSING, PROVED or DRAFT_ONLY | a FAILING or KNOWN_DEFECT row | rows uncovered, not run or unverified |
| `requirements_proved` | every row PROVED | a FAILING or KNOWN_DEFECT row | any row short of PROVED |
| `max_open_defects` | open defects within the limits | over a limit | no tracker, or tracker unreachable |
| `detectors_reached_verdict` | detectors reached verdicts | a detector reported held_empty | no detector in scope |
| `no_draft_evidence` | no requirement rests on draft tests alone | a DRAFT_ONLY row | the plan names no requirement |

`all` means the requirements the plan names plus those linked from the tests it selects.
By default the evaluation resolves the plan and judges only the selected tests;
`--no-resolve` judges every test in the runs.

## REST

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/plans` | plans with revision |
| GET | `/api/v1/plans/{id}` | body and revision |
| GET | `/api/v1/plans/{id}/selection` | resolved node ids (an external runner can execute the same scope) |
| POST | `/api/v1/plans/{id}/evaluations` | body `{"run_ids": [...]}`: evaluate against stored runs and keep it |
| GET | `/api/v1/plans/{id}/evaluations/latest` | the last stored evaluation |

Plans are edited by pull request, never over REST.

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `PLANS_DIR` | `plans` | where the REST layer and `load_plans()` look |
| `--rootdir` | `.` | suite to collect when resolving |
