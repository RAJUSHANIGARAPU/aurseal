# Release verdict

One answer per release candidate, `GO`, `GO_WITH_WAIVERS`, `NO_GO` or `NO_VERDICT`, with
every reason pointing at an exhibit (a run's integrity marker, a verdict row, a matrix
row, a defect id, a plan criterion). The verdict summarises evidence; it never invents
any. The code lives in `traceability/release.py`; the policy in `release-policy.yaml`.

## How the verdict is composed

**What it does.** In order:

1. **Run trust.** Only runs whose run-trust verdict is `PASS` are evidence. No
   such run: `NO_VERDICT` ("no run is evidence"), never `GO`. Other runs are listed as
   attention and ignored.
2. **Detector verdicts** over the evidence runs, worst per defect:
   `signature_mismatch` / `closed_regressed` block; `held_empty` is no verdict;
   `cleared` is attention (the defect looks fixed: close it and unwrap the detector). An
   unknown verdict kind is reported, never silently skipped.
3. **Requirement coverage** from the traceability matrix over the evidence runs (scoped to
   the plan's requirements when a plan is given). The policy decides per priority which
   statuses block and which are no verdict.
4. **Open defects** from the tracker (when the policy has a `defects` section): open ones
   at a blocking severity or label block. No tracker, or one that cannot be read, is no
   verdict, never "zero open".
5. **Plan exit criteria** (with `--plan`): `unmet` blocks, `no_evidence` is no verdict.

Then: any unwaived blocker → `NO_GO`; else any unwaived no-verdict → `NO_VERDICT`; else
any waiver used → `GO_WITH_WAIVERS`; else `GO`.

## Policy

`release-policy.yaml` (schema `traceability/schemas/release-policy.schema.json`), strict
by default:

```yaml
version: 1
default_priority: must
requirements:
  must:   {block: [FAILING, KNOWN_DEFECT], no_verdict: [UNCOVERED, NOT_RUN, UNVERIFIED, DRAFT_ONLY, PASSING]}
  should: {block: [FAILING], no_verdict: [UNCOVERED, NOT_RUN, UNVERIFIED, DRAFT_ONLY]}
  could:  {block: [], no_verdict: []}
detectors: {block: [signature_mismatch, closed_regressed], no_verdict: [held_empty], attention: [cleared]}
defects: {blocking_severities: [critical, high], blocking_labels: [blocker]}
```

Strict means a must-have requirement that is merely `PASSING` (a linked test passed, no
`proved` coverage verdict) is `NO_VERDICT`. Expect `NO_VERDICT` often at first: that is
accurate. Loosening it is a visible change to a versioned file, and the file's hash is
recorded with every verdict.

## Waivers

`release-waivers.yaml`, added and removed by pull request (schema
`release-waivers.schema.json`):

```yaml
waivers:
  - criterion: requirement:REQ-5      # or detector:BUG-142, defect:GH-57, plan:min_pass_rate, integrity
    owner: "@qa-lead"
    reason: proof lands with the next sprint's coverage tests
    expires: 2026-12-31
    release: R1                       # optional; omitted = any release
```

A live waiver turns one blocking or no-verdict reason into `waived` and the verdict into
`GO_WITH_WAIVERS`, printed with the owner, reason and expiry. An expired waiver is ignored
and the report says so.

## Determinism

The decision stores `inputs_hash`: SHA-256 over the candidate, the runs and their
integrity verdicts, the verdict rows, the matrix statuses, the open defects, the plan
evaluation, the policy and the live waivers. The same inputs always give the same hash and
the same verdict, so a verdict can be re-derived later.

## How to use

```bash
python -m traceability release decide --release R1 --candidate rc2 \
    --reports reports/ --plan plans/r1.yaml
python -m traceability release decide --release R1 --candidate rc2 \
    --db reports/results/results.db --save     # runs tagged with release label R1
```

Exit codes: `0` GO and GO_WITH_WAIVERS (the waivers are printed), `1` NO_GO,
`3` NO_VERDICT, `2` usage error. Writes `reports/release/<release>-<candidate>.json`
(schema `release-verdict.schema.json`) and `.md`.

| Method | Path | What |
|---|---|---|
| POST | `/api/v1/releases/{release}/candidates` | `{candidate_id, run_ids, version?, commit?, plan_id?}` |
| POST | `/api/v1/releases/{release}/candidates/{cid}/verdict` | compute from the stored runs and keep it (with a matrix snapshot) |
| GET | `/api/v1/releases/{release}/candidates/{cid}/verdict` | the latest stored verdict |

Waivers are file-only until the platform has writer roles; there is no waiver endpoint.

## Configuration

| Option | Default | Meaning |
|---|---|---|
| `--policy` | `release-policy.yaml` | policy file |
| `--waivers` | `release-waivers.yaml` | waivers file (absent = none) |
| `--plan` | — | fold a plan's exit criteria in and scope requirements to it |
| `--release-line` | — | the release line this candidate belongs to |
| `--known-defects` | — | known-defects manifest (file or directory) whose `fix_line` per defect scopes detectors |

A detector whose defect has a `fix_line` other than `--release-line` is listed under
**Out of scope**: it neither blocks nor counts as no verdict, because this release does
not contain that fix. On the test side, `@pytest.mark.known_defect(..., fix_line="2.4")`
with `pytest --release-line 2.3` (or `RELEASE_LINE=2.3`) deselects it from that gate; it
still runs on demand without the option.

## Honest limits

The verdict is a summary of evidence, not a decision-maker: who acts on `NO_VERDICT` is a
process question. It is only as good as the oracles behind `proved`.
