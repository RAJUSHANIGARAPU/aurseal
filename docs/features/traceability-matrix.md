# Traceability matrix

For every requirement, the matrix shows the tests linked to it and their latest result in
a scope, and gives the requirement exactly one status. The code lives in
`traceability/matrix.py` (statuses, rendering) and `traceability/evidence.py` (where
results come from). It is a report: gating happens in the release verdict.

## Statuses

**What it does.** Statuses are chosen by an explicit precedence so a row never overstates
the evidence:

| Status | Meaning |
|---|---|
| `UNCOVERED` | no test links to the requirement |
| `NOT_RUN` | linked tests exist, none executed in scope |
| `UNVERIFIED` | results exist only from runs whose run-trust verdict is FAIL: not evidence |
| `FAILING` | a linked test failed or errored, or its detector reported `cleared`, `signature_mismatch` or `closed_regressed` |
| `KNOWN_DEFECT` | linked tests fail, explained by an expected-red `reproduces` verdict or a `known_defect` xfail |
| `DRAFT_ONLY` | only generated tests awaiting review passed |
| `PASSING` | linked tests passed; no coverage verdict proved the requirement |
| `PROVED` | a passing, reviewed test recorded a `proved` coverage verdict naming the requirement |

`PASSING` is a claim (a link) plus a green test; only `PROVED` says the test asserted the
requirement. A matrix with no `PROVED` row says so in its headline
(`NOTHING IS PROVED: 0 of 6 requirements ...`) instead of showing a sea of `PASSING`.

Rows are listed problems first (failing, uncovered, unverified, not run, ...). Links to
ids that are not in the registry are listed separately.

**Latest result.** Per test, the newest *executed* result in scope; a newer skip does not
erase an older executed answer. Runs from all lanes in scope are considered.

**Stale.** A row is `stale` when its result came from an older revision of the test than
the current one (case versioning). Revisions are recorded in the store only, so a matrix
built from artefacts reports staleness as *not measured*, never as fresh.

## Sources and scope

| Source | Reads | Scope |
|---|---|---|
| `ArtifactResultSource(reports_dir)` | `run-trust*.json`, `verdicts*.jsonl`, `case-links*.json` anywhere under the directory | the runs found there (optionally `run_ids`) |
| `StoreResultSource(store, scope)` | `runs`, `final_results`, `verdicts`, `case_links` | run ids, or a branch (`last` N runs), or a release label |

An unscoped store request is refused (`ValueError`, REST 400): "latest result" across
branches and environments would mislead.

## How to use

```bash
# From a run's own files (CI offline job, no database)
python -m traceability matrix --reports reports/ --out reports/traceability/

# From the store, for explicit runs or the last 5 runs on main; --save keeps a snapshot
python -m traceability matrix --db reports/results/results.db --runs 1234-1
python -m traceability matrix --db reports/results/results.db --branch main --last 5 --save
```

Writes `matrix.json` (schema `traceability/schemas/matrix.schema.json`) and `matrix.md`,
and prints the headline. Python:

```python
from traceability.evidence import ArtifactResultSource
from traceability.matrix import build_matrix, render_markdown
from traceability.requirements import Registry

m = build_matrix(Registry.load(), None, ArtifactResultSource(Path("reports")))
print(render_markdown(m))
```

| Method | Path | What |
|---|---|---|
| GET | `/api/v1/traceability/matrix?runs=&branch=&last=&release=&requirements=&format=json\|md` | compute for a scope |
| GET | `/api/v1/traceability/requirements/{id}?runs=...` | one row with its cases and the requirement |

## Configuration

| Variable / option | Default | Meaning |
|---|---|---|
| `--registry` / `REQUIREMENTS_FILE` | repository registry | requirements to list |
| `--requirements` | all active | restrict rows (a plan's requirements) |
| `TRACEABILITY_DIR` | `reports/traceability` | where the plugin wrote `case-links.json` |

## Honest limits

The matrix shows absence of evidence; it cannot grade a weak assertion. Coverage quality is
only as good as the oracles behind `proved`.
