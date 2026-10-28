# CI workflow

The workflows under `.github/workflows/` have a deliberate shape, and each property a
reviewer would otherwise have to remember is checked offline by `gate.workflow_lint`, so a
careless edit fails a test instead of silently changing behaviour. Around that shape sit
the run-time pieces: manual-run inputs, per-environment credentials, an external config
binding, an opt-in token lane, evidence publishing and a failure notification.

| Workflow | Triggers |
|---|---|
| `tests.yml` | push to `main` and `bringup/**`, pull requests into `main`, `workflow_dispatch`, nightly `schedule` |
| `secret-scan.yml` | every push on every branch, every pull request |
| `mutation.yml` | nightly `schedule`, `workflow_dispatch` |
| `release.yml` | `v*` tags, `workflow_dispatch` |

A feature-branch push that is neither `main` nor `bringup/**` starts only the secret scan.

---

## Workflow shape

### What it does

`python -m gate.workflow_lint` (also the pre-commit hook on workflow files, and
`tests/gate/test_workflow_shape.py` over the real workflows) applies, on top of the
structure rules:

| Rule | Finding when |
|---|---|
| Concurrency | no top-level `concurrency`; `cancel-in-progress: true` unconditionally; an expression that is not limited to pull requests (a cancelled default-branch run leaves no verdict) |
| Job timeout | a job has no `timeout-minutes` |
| Run budget | the longest `needs` chain, summed by `timeout-minutes`, exceeds the budget (Actions has no whole-run timeout); the finding names the path, e.g. `A, B, C = 70 min` |
| Retention | an `upload-artifact` step has no `retention-days`; a heavy artefact (path segment `traces`, `videos`, `screenshots`, `reports`, `evidence`) is kept longer than 7 days; any artefact longer than 30 |
| Browser container | the Playwright image is not an exact version tag, or its version differs from `playwright==` in `requirements.txt` (both named); no `--ipc=host` or `--shm-size` of at least 1g; a step-level `github.ref` condition (gate the job, so a skipped job never pulls the image) |
| Nightly | a `schedule` cron fires on the hour; in a scheduled workflow a job (other than the secret scan) does not read `vars.NIGHTLY_ENABLED` |
| Secret scan | see [Secret scan](#secret-scan) |
| Dispatch queue | a `repository_dispatch`/`workflow_dispatch` workflow's concurrency group can evaluate to `tests.yml`'s (`github.workflow` filled in with each workflow's name), so it would queue behind the merge gate |

### How to use

```bash
python -m gate.workflow_lint                      # every workflow, shape rules on
python -m gate.workflow_lint --run-budget 45      # tighter budget
python -m gate.workflow_lint --structure-only     # the parse/structure rules only
```

### Configuration

| Flag | Default | Meaning |
|---|---|---|
| `--run-budget` | `60` | whole-run budget in minutes (critical path) |
| `--short-retention` | `7` | max days for heavy artefacts |
| `--long-retention` | `30` | max days for any artefact |
| `--heavy` | `traces,videos,screenshots,reports,evidence` | path segments that make an upload heavy |
| `--requirements` | `requirements.txt` next to `.github/` | file holding the `playwright==` pin |

`ShapeOptions.budget_overrides` gives `mutation.yml` its own 90-minute budget: one job of
that length is the whole point of that workflow, and it lives apart so `tests.yml` keeps
the 60-minute budget.

### Example

```python
from gate.workflow_lint import critical_path

critical_path({"A": {"timeout-minutes": 10}, "B": {"timeout-minutes": 30, "needs": "A"}})
# (['A', 'B'], 40)
```

---

## Nightly switch

### What it does

The nightly cron is off the hour (`17 2 * * *`). Every job's `if:` reads the repository
variable `NIGHTLY_ENABLED` (unset means on). The `nightly` job runs on every scheduled run
and writes one line to the step summary: either that the nightly is disabled (and by which
variable), or which opt-in jobs could not run on this schedule and why — so an absence is
recorded every night instead of looking like a quiet green. The secret scan is the one job
the switch never stops.

### How to use

Turn the nightly off: repository variable `NIGHTLY_ENABLED=false`. Turn it back on: delete
the variable or set it to `true`.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NIGHTLY_ENABLED` | on | `false` skips every scheduled job but the note |
| `RENDER_TOKEN_PRESENT` | set by the workflow | whether the token lane can run tonight |

### Example

```text
Nightly: not run on a schedule - bringup (runs only on bringup/<test> branches); pr-feedback (pull requests only); render (needs a bearer token and the RENDER_TOKEN secret is not set); visual-baselines (manual only: ...).
```

---

## Secret scan

### What it does

`secret-scan.yml` runs gitleaks over the full git history on every push of every branch
and every pull request. It is a root job (no `needs:`), has no `if:`, checks out with
`fetch-depth: 0`, downloads gitleaks at a pinned version verified by SHA-256 before it runs,
uses `.gitleaks.toml` with `--redact`, and a finding fails the job. The lint fails a change
that adds an `if:`, a `needs:`, `continue-on-error`, `|| true` or a shallow checkout to the
scan job, and fails a workflow directory with no scan job at all.

### How to use

```bash
gitleaks git . --config .gitleaks.toml --redact --no-banner
```

### Configuration

Scanner version and checksum are literals in the install step; the rules and the narrowly
scoped allow-lists are in `.gitleaks.toml`. See [security testing](security-testing.md).

### Example

`tests/gate/test_workflow_shape.py` builds a scratch repository holding an invented key
(assembled at run time), proves the scan fails, then allow-lists exactly that value and
proves it passes (skipped when gitleaks is not installed).

---

## Manual-run inputs

### What it does

A manually started run accepts typed inputs; `gate.run_inputs` validates every one before
any test starts and turns them into the gate command and environment. Default inputs
produce exactly the literal `Run Smoke Tests` step (a test pins it), so the CI selection
gate keeps auditing a literal command; anything else runs through
`python -m gate.run_inputs run`.

| Input | Default | Effect |
|---|---|---|
| `env` | `qa` | `TEST_ENV`; must be declared in `config/environments.json` |
| `preset` | `all` | `all` keeps the gate base `smoke or api or contract`; `smoke`/`regression` replace it; the gate exclusions always apply |
| `auth_mode` | `configured` | `AUTH_MODE` (`none`, `ui`, `password`, `token`); `configured` keeps the environment's own |
| `token` | empty | `SUPPLIED_TOKEN`, only with `auth_mode=token`; masked from the event payload, never on the command line |
| `headless` | `true` | `HEADLESS=false` when off |
| `step_screenshots` | `false` | `ALLURE_STEP_SCREENSHOTS=true` |
| `lanes` | empty | opt-in lanes; `slow`/`on_demand` drop their exclusion and set their toggle; `regression`, `checks`, `browser-matrix` switch workflow steps on |
| `selection` | empty | node ids/globs (bring-up grammar): no exclusions, no reruns, sequential, `COVERAGE_VERDICT_REQUIRED=true`; wins over `preset` with a notice |
| `keep_all_evidence` | `false` | `TRACE_ALWAYS=true`, `VIDEO_ALWAYS=true` |
| `strict_oracles` | `false` | `STRICT_ORACLES=true` for assertions proven offline but not yet watched live |
| `require_config_binding` | `false` | `EXT_CONFIG_REQUIRED=true` |
| `seed_mode` | `fresh` | `SEED_MODE` (`fresh` adds nothing) |
| `entity_allowlist` | empty | `ENTITY_ALLOWLIST`; only with `seed_mode=targets`, pattern `[A-Za-z0-9._-]+(,...)*` |
| `browser_matrix` | `false` | the matrix job |
| `run_token_lane` | `false` | the render job |
| `shards` | `1` | sharding pilot: `2`-`4` run the gate selection on the `browser-shard` matrix instead of the browser job; needs `seed_mode=fresh`, no `selection`, no bring-up ref, no `RELEASE_CANDIDATE` |
| `shard_users` | empty | one login per shard (`LOGIN_USERNAME` of shard k); its length must equal `shards`, and it must be empty with one shard |

An unknown lane, an undeclared environment, a token without token mode, token mode without a
token, targets without `targets` mode (or the reverse) — each fails with a message naming
the input, exit 2, nothing started.

### How to use

```bash
python -m gate.run_inputs run --dry-run --preset smoke --lanes slow
python -m gate.run_inputs run --selection "tests/gate/test_lanes.py" --env staging
INPUT_SHARDS=3 python -m gate.run_inputs plan-shards     # the plan, as the shard-plan job writes it
INPUT_SHARDS=3 python -m gate.run_inputs run --dry-run --shard 2/3
GITHUB_EVENT_NAME=workflow_dispatch INPUT_ENV=staging python -m gate.run_inputs post-deploy
```

`post-deploy` validates a post-deploy trigger (`post-deploy.yml`, see
`docs/ci-decisions.md#post-deploy-runs`) and, with `--execute`, runs the preset with
`--reruns 0`.

### Configuration

In the workflow every input reaches the step as `INPUT_<NAME>`; locally each is a flag
(`--entity-allowlist a,b`). `resolve` exports the environment to `$GITHUB_ENV`, prints the final
command (token masked) to the log and the step summary, and writes `custom=true|false`.

### Example

```text
command: pytest -m '(smoke) and not unit and not on_demand and not draft and not render_check' -n auto --browser-channel=chrome --junitxml=reports/junit-browser.xml
environment: {"RUN_SLOW_TESTS": "true", "TEST_ENV": "qa"}
```

---

## Per-environment credentials

### What it does

Each target environment names its login variables in `config/environments.json`
(`credentials`: `username_var`, `password_var`, `shared`). In Actions each target is a GitHub
Environment holding those names, selected by `environment: ${{ inputs.env || 'qa' }}`. The
preflight step — the first after install in the browser, matrix and bring-up jobs — exits 0
when the values the environment's auth mode needs are present (`none` needs nothing,
`token` needs `SUPPLIED_TOKEN`), exports them as `AUTH_USERNAME`/`AUTH_PASSWORD`, and
otherwise fails at once naming the environment, the missing variable names and the GitHub
Environment to set them in. It never prints a value. An environment marked
`"shared": false` fails rather than use the repository-level shared login.

### How to use

```bash
python -m gate.credentials preflight --env staging
```

### Configuration

| Name | Where | Meaning |
|---|---|---|
| `LOGIN_USERNAME` | Environment variable (`vars`) | username, never a literal in the workflow |
| `LOGIN_PASSWORD` | Environment secret | password |
| `SHARED_LOGIN_USERNAME` / `SHARED_LOGIN_PASSWORD` | repository variable / secret | fallback for `"shared": true` environments only |

### Example

```text
credentials for 'staging' (auth mode ui): missing LOGIN_PASSWORD (a shared login is set, but 'staging' is marked shared: false and must have its own); set them as secrets/variables of the GitHub Environment 'staging'
```

---

## External config binding

### What it does

The offline job checks a configuration repository out into `ext-config/` (`fetch-depth: 1`,
default: this repository's default branch, so it works with zero setup), and
`python -m gate.external_config verify` decides: **bound** (a sentinel file exists and is
non-empty) exports `EXT_CONFIG_PATH`, `EXT_CONFIG_PINNED`, `EXT_CONFIG_COMMIT` to the
binding tests (`tests/external_config/`), shows repo, ref and the 8-character commit in the
step summary, and the report environment panel records the full commit
(`External.Config.Commit`). **Not bound** emits a `::warning::`, a summary block listing the
checks that did not run (`tests/external_config/checks.txt`) and the likely causes, and an
`ext-config` run-trust marker with verdict FAIL / enforcement UNSTABLE, so the run cannot
look fully green; with `require_config_binding` the step fails instead. The checkout is
removed after the tests and is in `.gitignore`.

### How to use

```python
from gate.external_config import bound_checkout

path, commit = bound_checkout()  # skips, or fails when EXT_CONFIG_REQUIRED=true
```

### Configuration

| Name | Default | Meaning |
|---|---|---|
| `vars.EXT_CONFIG_REPO` | this repository | `owner/name` to check out |
| `vars.EXT_CONFIG_REF` | its default branch | branch or tag |
| `vars.EXT_CONFIG_SUB_PATH` | `demo_sut/config` | config directory inside the checkout |
| `vars.EXT_CONFIG_SENTINEL` | `subscription.machine.yaml` | file that must exist and be non-empty |
| `secrets.EXT_CONFIG_TOKEN` | `github.token` | read token for a private repository (never reaches the tests) |
| `EXT_CONFIG_REQUIRED` | `false` | an unbound checkout fails instead of skipping |

### Example

```text
External config bound: owner/repo@main commit 1c7bd71a
```

---

## Token-only lane

### What it does

The document-render checks (`render_check`, `tests/render_lane/`) authenticate only with a
bearer token. They run in their own `render` job: on dispatch with `run_token_lane`, or
nightly on `main` when the `RENDER_TOKEN` secret is set. The first step refuses (exit 2, no
test started) unless the auth mode is `token` and a token is present. The token is masked,
reaches pytest through the environment only, the lane runs its marker with no reruns and
`RENDER_REQUIRED=true` (an unreachable renderer fails, it does not skip), and writes to
`reports/allure-results-render/` and `reports/junit-render.xml` with its own run-trust lane.
The marker is excluded from the default gate.

### How to use

```bash
AUTH_MODE=token SUPPLIED_TOKEN=... python -m gate.token_lane run
```

Without `RENDER_BASE_URL` the tests start the demo service in-process with authentication
on and the supplied token registered.

### Configuration

| Name | Meaning |
|---|---|
| `run_token_lane`, `auth_mode=token`, `token` | dispatch inputs |
| `secrets.RENDER_TOKEN` | token for scheduled runs (absent = never on a schedule) |
| `RENDER_BASE_URL` | a running render service instead of the in-process demo |
| `RENDER_REQUIRED` | set by the job: abstention fails |

### Example

```text
token lane: the render lane needs auth mode 'token' (got 'none'). Start the run with auth_mode=token and a token, or leave the lane off.
```

---

## Evidence publishing

### What it does

Every test job ends with `python -m gate.evidence stage --job <name>` and an
`if: always()` upload of `evidence/` named `evidence-<job>` (7 days). Staging writes the
report categories file into each report-results directory and copies one fixed list of
paths (`gate.evidence.EVIDENCE`: report results, screenshots, traces, videos, JUnit XML,
failure bundles, logs, flakiness, detector status, run-trust markers, traceability,
coverage gaps, defect drafts, release verdict). The `publish` job (`needs` every test job,
`if: always()`) downloads `evidence-*`, merges report results and JUnit files without
overwriting (a same-named file from another lane becomes `evidence-<lane>--<name>`, and the
rename is logged), renders one static HTML report with the framework's own dashboard
renderer (`results` store imported from the JUnit files; no Java needed), and writes the
summary: every lane's run-trust verdict first, totals per lane, failing tests (30, then
"N more"), and one `absent:` line per job that ran but left no evidence. Missing input never
fails the job.

### How to use

```bash
python -m gate.evidence stage --job offline
python -m gate.evidence publish --in evidence-in --out reports/published --expected offline,browser
```

### Configuration

| Flag | Default | Meaning |
|---|---|---|
| `--cap` | `30` | failing tests listed in the summary |
| `--needs-json` | — | `toJSON(needs)`: a skipped job is not expected to upload |
| `--no-render` | off | skip the HTML report |

### Example

```text
## Evidence

- offline: VERDICT=PASS ENFORCEMENT=FAIL RUN=123-1 LANE=offline
| Lane | Tests | Failures | Errors | Skipped |
absent: evidence-browser (the job ran but uploaded nothing, or died before uploading)
```

---

## Failure notification

### What it does

The `notify` job (`needs` every test job and `publish`, `if: always()`) sends one message
through an incoming webhook when a default-branch or scheduled run failed (`failed`), or
nothing failed but a lane's verdict is FAIL under `unstable` enforcement or a job was
cancelled (`not evidence`). The text carries the status, repository, branch, the one-line
run description (verdicts, environment, external-config commit, `nightly` prefix) and the
run URL. One POST, 10 s timeout, one retry on a 5xx or timeout; a final failure is a
`::warning::`. Without the secret it logs "notification not configured" and passes. The
webhook URL is redacted from every message.

### How to use

```bash
python -m gate.notify describe --reports evidence-in --env qa
python -m gate.notify send --reports evidence-in --needs-json '{"offline": {"result": "failure"}}'
```

### Configuration

| Name | Default | Meaning |
|---|---|---|
| `secrets.NOTIFY_WEBHOOK_URL` | unset | incoming webhook; unset = no message |
| `vars.NOTIFY_ALL_BRANCHES` | `false` | notify for every branch, not only `main` and schedules |
| `--max-chars` | `500` | description length before the ellipsis |
| `--timeout` | `10` | seconds per attempt |

### Example

```text
[not evidence] owner/repo main: VERDICT=FAIL ENFORCEMENT=UNSTABLE RUN=123-1 LANE=browser | env qa | config 1c7bd71a https://github.com/owner/repo/actions/runs/123
```
