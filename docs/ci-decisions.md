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

## Package

**Rule.** The installable package (`src/aurseal`) is installed in the offline job with
`pip install -e .`, and its tests (`tests/package/`) run in the offline suite, where the
CI-selection gate sees them. `package.yml` adds only what that job cannot: `mypy --strict`
on Python 3.12, a build whose wheel must hold nothing but `aurseal/`, and a second run of
`tests/package` with every generated project's dependencies and Chromium installed. That
run fails when any test skips, because there a skip means a check ran nowhere.

**Prevents.** Package tests that no gate audits; a wheel or sdist that ships the framework,
the demo system or local tooling config; a template whose generated project only ever
renders, because its own type check and offline tests skip in the offline job for lack of
its dependencies.

**Change safely.** Keep `tests/package/` collectable only when the package is installed (its
conftest skips collection otherwise). Never put `src` on the pytest path: the tests must
import the installed package. Package release tags are `aurseal-v*`, because plain `v*`
fires `release.yml`; `publish.yml` builds, checks and publishes only on `aurseal-v*`, with
trusted publishing (`id-token: write` on the publish jobs alone, environment `pypi`), and
its wheel check is the same `tools/wheel_check.py` that `package.yml` runs. Process:
[Releasing](kit/releasing.md).

## Leak check

**Rule.** `leak-check.yml` runs `python -m aurseal.leakgate` on every pull request (tree,
title, body, commit messages, added lines), on every push to `main` (tree) and weekly
(tree, plus the whole history against the denylist). The denylist is the repository secret
`LEAK_DENYLIST` and never a file in the repository. Without it a branch of this repository
fails closed; a fork or bot pull request runs the built-in personal-data rules and reports
the denylist as NOT ASSESSED until a maintainer dispatches the workflow with `pr=<number>`.
Findings print a location and a rule id, never the matched text. `pull_request_target` is
never used.

**Prevents.** A private name, an employer name or personal data reaching `main` unnoticed;
the check itself republishing what it found.

**Change safely.** A personal-data false positive goes in `leakgate-allow.toml` with a
reason; a denylist false positive is fixed in the private list. Run the same scan locally
with `python -m aurseal.leakgate scan --tree .` before pushing: on a public repository a
pushed branch is already published, so the CI run is detection, not prevention.

## Examples lane

**Rule.** The projects under `examples/` run in two lanes, and only the local one runs on a
pull request.

- **Local, per pull request.** `examples-pr.yml` runs when a pull request touches
  `examples/` or `src/aurseal/templates/`: a matrix job per example runs its
  `make test-local`, which starts a copy of the target in Docker, runs the suite against it
  and removes it. No test traffic leaves the runner. Before the suite starts, `test-local`
  refuses any `APP_*URL` or `TARGET_URL` in its environment whose host is not `localhost`,
  `127.0.0.1` or a Compose service name, and the workflow makes the same check on what
  `make local-env` reports. The browser targets also send a Content-Security-Policy that
  keeps the page on its own origin, so an app's web fonts or error reporting are blocked,
  not fetched. Images, the pinned upstream source and packages are still downloaded to
  build the targets; that is setup, not the system under test. Reports go to
  `reports/local/`, are named `example-<name>-local` and say "local" in the report.
- **Live, nightly.** `examples-nightly.yml` (nightly and on dispatch) runs the same suites
  against the public demo sites. A failing example opens or comments on one
  `live-drift: <example>` issue; the run itself stays green.

Not every local target is the live one. Restful Booker runs from the image its author
publishes; Countries, from its upstream source at a pinned commit; Sauce Demo, from
Sauce Labs' `sample-app-web` at a pinned commit; QuickPizza, from the `quickpizza-local`
image. The W3C demo's pages are not copied (its document licence allows unmodified copies
and no derivatives, too narrow a grant to vendor a site on), so `a11y-w3c-bad` scans its
own fixtures at the demo's paths and leaves out the tests that assert on the demo itself.
The perf examples run only their smoke profile per pull request.

`make test-local` lives in a `GNUmakefile` that hands every other target to `Makefile`:
the Makefile is template-owned, and an edit to it would fail the template check below.

The example suites are not part of this repository's own test suite: the root
`pytest.ini` `testpaths` leave `examples/` out, and each example carries its own pytest
configuration in its `pyproject.toml`, so `pytest` from the root never collects them and
the CI-selection gate never counts them. Without network,
`tests/package/test_templates_render.py` checks each example's template-owned files (its
`.scaffold-manifest`) against a fresh render wherever the package and Copier are installed.

**Prevents.** A third-party outage turning a pull request red; a change to an example
reaching `main` without its tests having run once; an example quietly drifting from the
template it claims to come from.

**Change safely.** A new example gets a `GNUmakefile` with `test-local` and `local-env`, a
`docker-compose.local.yml`, a row in the `examples-pr.yml` matrix, its own job in
`examples-nightly.yml` and a `.scaffold-manifest`. Pin every image by digest and every
upstream source by commit. Never point `test-local` at a public host, and never add
`examples` to `testpaths`, which would put network tests into the offline job.

## Docs site

**Rule.** `pages.yml` builds the site from `docs/` with `mkdocs build --strict` on every
pull request that touches the docs, the README, the MkDocs configuration or the examples,
and publishes it to GitHub Pages from `main` and after each nightly examples run. A
build-time hook (`tools/docs_site/hooks.py`) rewrites links that leave `docs/` to the file
on the repository host after checking the file exists, checks that a link into `reports/`
names an example, and checks the README's links before the build. The example reports
are the nightly run's artifacts, never a fresh call to a third-party site; an example
without a report in the last 7 days gets a page that says so. `requirements-docs.txt`
pins exact versions of MkDocs (1.x) and its theme.

**Prevents.** A broken link or a renamed page reaching the published site; a pull request
turning red because a public demo site is down; a theme or MkDocs release changing the
build under an unrelated change.

**Change safely.** A new page goes in the `nav` of `mkdocs.yml`, or under `not_in_nav`
or `exclude_docs` with a reason. Build locally before pushing:
`pip install -r requirements-docs.txt`, then `mkdocs build --strict`. The README check
runs in the offline suite too (`tests/tools/test_docs_site_hooks.py`).
