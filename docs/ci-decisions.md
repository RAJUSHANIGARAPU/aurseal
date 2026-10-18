# CI decisions

One section per CI rule: the rule, the failure it prevents, and how to change it safely.
The workflow files link here instead of repeating the rationale;
`tests/test_docs_drift.py` checks that every section is linked and every link resolves.

## Lanes

**Rule.** The framework's offline suite runs first in its own job; product checks run after
it. Opt-in lanes (`slow`, `on_demand`), `draft` and `render_check` are excluded from the
gate unless their toggle is set (`gate.lanes`).

**Prevents.** A broken harness reading as a product defect; a test nobody has watched pass
reddening the gate; a generated test gating before review.

**Change safely.** Add a lane in `gate.lanes.LANES` and its literal pytest step in
`tests.yml` in the same change; `tests/test_ci_selection.py` fails otherwise.

## Reruns

**Rule.** At most one rerun in the gate lane, merged into the run's verdict by
`TEST_RUN_ID`; bring-up and the token lane run with no rerun.

**Prevents.** A flake hidden by retries; a retried subset replacing the whole run's
verdict.

**Change safely.** Never raise the count to make a lane green; fix or quarantine the flake.

## Empty selection

**Rule.** pytest's exit 5 (selected nothing) is never masked; `gate.workflow_lint` fails
`|| true` and `|| [ $? -eq 5 ]` after a pytest command.

**Prevents.** A marker typo producing a green run that ran nothing.

**Change safely.** If selecting nothing is expected, skip the step with an `if:` that says
why (see the drafts job), never mask the status.

## Run trust

**Rule.** Every lane writes a run-trust verdict (`gate.integrity`); `fail` enforcement
fails the job, `unstable` annotates it, `report` records it.

**Prevents.** A run that executed 8 of 40 tests and skipped the rest reading as green.

**Change safely.** Move a lane from `unstable` to `fail` once it has been green for a while;
lowering enforcement never turns the finding off.

## Bring-up branches

**Rule.** A push to `bringup/<test path>` runs exactly that test once, with every exclusion
dropped, no reruns and abstention turned into failure.

**Prevents.** A never-run test entering the gate unwatched.

**Change safely.** The selection grammar lives in `gate.lanes.resolve_selection`; manual
runs with `selection` reuse it.

## Concurrency and time budget

**Rule.** One run per workflow and ref; pull-request runs cancel superseded ones, a
default-branch run is never cancelled. Every job has `timeout-minutes`, and the critical
path of `tests.yml` stays within 60 minutes.

**Prevents.** A cancelled default-branch run leaving no verdict; a hung job holding a runner
for six hours; a pipeline that slowly grows past anyone's patience.

**Change safely.** Raise a job's timeout only with the budget in view;
`python -m gate.workflow_lint` names the critical path.

## Nightly

**Rule.** The cron is off the hour; the repository variable `NIGHTLY_ENABLED=false`
switches every scheduled job off except the note that says so; every scheduled run writes
which opt-in jobs it could not run.

**Prevents.** The nightly silently stopping, or an opt-in lane's absence reading as a quiet
green night.

**Change safely.** Add a new scheduled job with the switch in its `if:` (the lint requires
it) and, if it cannot always run, a reason in `gate.nightly.SCHEDULE_SKIPPED`.

## Secret scan

**Rule.** gitleaks over the full history on every push of every branch and every pull
request, a root job nothing can skip, pinned and checksum-verified, redacted output.

**Prevents.** A secret pushed on a feature or bring-up branch going unnoticed until merge.

**Change safely.** Upgrade by changing version and checksum together; narrow false positives
in `.gitleaks.toml` by exact value and file.

## Manual-run inputs

**Rule.** Dispatch inputs are validated by `gate.run_inputs` before any test starts; an
input that would be ignored is an error; default inputs reproduce the literal gate step.

**Prevents.** A manual run that silently ignored its token, targets or lane and reported
green on something else.

**Change safely.** A new input is declared in the workflow and in `gate.run_inputs.INPUTS`
together; a test fails on an orphan on either side.

## Credentials

**Rule.** Each target environment has its own login, checked by
`python -m gate.credentials preflight` before any test; an environment marked
`shared: false` never falls back to the shared login.

**Prevents.** A missing secret surfacing half an hour later as a login timeout that looks
like a product problem; a run silently logging in as the wrong user.

**Change safely.** Add the variables to the GitHub Environment first, then the
`credentials` block in `config/environments.json`.

## External config

**Rule.** The external configuration is checked out shallow, verified by a sentinel, and
reported as bound (commit shown) or not bound (warning, UNSTABLE lane verdict, or a failure
when required).

**Prevents.** Binding tests skipping silently while the run looks fully green.

**Change safely.** Point `EXT_CONFIG_REPO`/`EXT_CONFIG_REF` elsewhere and update the
sentinel; set `require_config_binding` once the binding is reliable.

## Token lane

**Rule.** Bearer-token-only checks run in their own job, only when asked for with a token,
masked, no reruns, required switch on, results in their own directories.

**Prevents.** A lane asked to run without a token skipping everything and going green; its
results overwriting the gate's.

**Change safely.** Keep `gate.token_lane.build_command` and the literal workflow step equal
(a test pins them).

## Evidence publishing

**Rule.** Every test job uploads `evidence-<job>` with `if: always()`; one publish job
merges everything without overwriting and lists missing artefacts.

**Prevents.** A red or cancelled run leaving nothing to read; a dead lane disappearing
without a trace; two lanes' same-named files overwriting each other.

**Change safely.** Add paths to `gate.evidence.EVIDENCE` (one list), not to the workflow.

## Notification

**Rule.** One message for a red or not-evidence run on the default branch or a schedule,
only when the webhook secret is set; delivery failure is a warning.

**Prevents.** A red nightly nobody looks at; a missing channel reddening a run.

**Change safely.** `NOTIFY_ALL_BRANCHES=true` widens it; the webhook URL never appears in a
log.
