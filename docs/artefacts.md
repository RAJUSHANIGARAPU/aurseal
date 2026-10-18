# Artefacts a run writes

Every machine-readable file a run leaves under `reports/`: who writes it, its shape, who
reads it, and how long CI keeps it. A small, realistic sample of each lives in
[`artefact-samples/`](artefact-samples/), generated from the fixed sample run
(`scripts/artefact_samples/sample_suite.py`: one pass, one failure, one skip, one
expected-red detector) by `python -m reporting.artefact_samples` — never edited by hand.
`tests/reporting/test_artefact_samples.py` parses each sample with the reader its real
consumer uses, so a format change shows up as a sample diff and a failing test.

Regenerate after a deliberate format change, and check in CI or locally:

```bash
python -m reporting.artefact_samples            # rewrite docs/artefact-samples/
python -m reporting.artefact_samples --check    # regenerate to a temp dir and diff
```

Samples are normalised: timestamps, durations, UUIDs, host names, paths, commit hashes and
interpreter versions become fixed placeholders. In CI every artefact travels inside the
job's `evidence-<job>` upload (7 days, [evidence publishing](features/ci-workflow.md#evidence-publishing));
results bundles also go up as `results-<job>` (7 days) and onto the results-history branch.

## Index

| Artefact | Producer | Path under `reports/` | Format | Written | Consumers | Sample |
|---|---|---|---|---|---|---|
| Report results | allure-pytest via `reporting.pytest_plugin` | `allure-results/*-result.json` | JSON per test | every test | `gate.evidence`, `gate.merge`, Allure | `allure-results/` |
| Report categories | `reporting.allure_categories` | `allure-results/categories.json` | JSON list | session start, and `gate.evidence` stage | `gate.merge`, Allure | `allure-categories.json` |
| Report environment | `reporting.allure_environment` | `allure-results/environment.properties` | Java properties | session start | `gate.merge`, Allure | `allure-environment.properties` |
| Report executor | `reporting.allure_environment` | `allure-results/executor.json` | JSON | session start | Allure | `allure-executor.json` |
| JUnit XML | pytest `--junitxml` | `junit-*.xml` | JUnit XML | session end | `gate.evidence`, `gate.merge`, `results.io.junit` | `junit.xml` |
| Run-trust marker (text) | `gate.integrity` | `run-trust[-<lane>].txt` | first line `VERDICT=... ENFORCEMENT=... RUN=... LANE=...`, then a box | session end | `gate.integrity`, `gate.notify`, `gate.evidence` | `run-trust.txt` |
| Run-trust marker (JSON) | `gate.integrity` | `run-trust[-<lane>].json` | JSON | session end | `gate.integrity`, `traceability.pr_comment` | `run-trust.excerpt.json` |
| Detector verdicts | `verification.verdicts` | `verdicts/verdicts.jsonl` | JSON lines | per detector | `gate.verdicts`, `gate.integrity`, `traceability.pr_comment` | `verdicts.jsonl` |
| Detector status | `verification.status_report` | `verdicts/verdict-status.md` | Markdown | session end | people, PR comment | `verdict-status.md` |
| Case links | `traceability.pytest_plugin` | `traceability/case-links[-<lane>].json` | JSON | session end | `traceability.links`, `traceability.matrix` | `case-links.json` |
| Failure bundle | `core.failure_reporter` | `failures/<test>-<stamp>.json` | JSON | per failed test | `autonomous_ui.models`, `autonomous_ui.orchestrator` | `failure-bundle.excerpt.json` |
| Flakiness history | `autonomous_ui.flakiness.history_store` | `flakiness/history.jsonl` | JSON lines, append-only | per test | `autonomous_ui.flakiness.history_store`, `autonomous_ui.flakiness.detector` | `flakiness-history.excerpt.jsonl` |
| Results bundle | `results.bundle` | `results/bundle-<run>.ndjson` | NDJSON records (`run`, `case`, `revision`, `invocation`, `result`, `verdict`, `run_end`) | `python -m results export bundle` | `results.bundle`, results-history job | `results-bundle.excerpt.ndjson` |
| Traces, videos, screenshots | `core.artifact_policy` | `traces/`, `videos/`, `screenshots/` | ZIP, WebM, PNG | failed browser tests | people, `autonomous_ui.orchestrator` | no sample: only a browser run produces them |
| Locator heal proposals | `autonomous_ui.locator_store` | `ui/locators/<name>.heals-pending.json` (git-ignored) | JSON | a browser heal | reviewer, `autonomous_ui.locator_store` | no sample: only a browser heal produces them |
| Heal events | `autonomous_ui.heal_events` | `healing/healing-events.jsonl` | JSON lines | a browser heal | people | no sample: only a browser heal produces them |
| Fixture provenance | `gate.provenance` | `data-origin.jsonl` | JSON lines | `seeded` tests | `gate.integrity` | no sample: the sample run has no `seeded` test |
| Results store | `results.store` | `results/results.db` | SQLite | every run | `results` CLI, dashboard | no sample: binary; the bundle is its portable form |

## Shapes

### Report results (`allure-results/*-result.json`)

| Field | Type | Meaning | Example |
|---|---|---|---|
| `name` | string | test function | `test_a_failing_expectation` |
| `status` | string | `passed`, `failed`, `broken`, `skipped` (an expected-red detector is `skipped` with an XFAIL message) | `failed` |
| `statusDetails.message` | string | failure or skip message | `AssertionError: ...` |
| `labels` | list of `{name, value}` | suite, host, thread, framework | `{"name": "suite", "value": "sample_suite"}` |
| `start`, `stop` | integer | epoch milliseconds | `0` in samples |
| `historyId`, `testCaseId` | string | stable hash of the test identity | |

### Report categories (`categories.json`)

List of `{name, matchedStatuses, messageRegex?}`; first match wins
(`reporting.allure_categories.categorize`).

### Report environment and executor

`environment.properties`: `Environment`, `Base.URL`, `Browser`, `Headless`,
`Playwright.Version`, `Python.Version`, `OS`, `Auth.Mode`, `Git.Branch`, `Git.Commit`,
`Run.Mode`, `Run.Id`, `Run.Timestamp`, and `External.Config.Commit` when an external
configuration was bound. `executor.json`: `name`, `type`, `buildName`, `reportName`
(plus `buildUrl` in CI).

### JUnit XML

`<testsuites><testsuite tests failures errors skipped time><testcase classname name time>`
with a `<failure>`, `<error>` or `<skipped>` child.

### Run-trust markers

Text: first line `VERDICT=<PASS|FAIL|UNJUDGED> ENFORCEMENT=<FAIL|UNSTABLE|REPORT> RUN=<id>
LANE=<lane>`, then the boxed summary. JSON fields: `verdict`, `enforcement`, `headline`,
`timestamp`, `run_id`, `lane`, `selected`, `executed`, `skipped`, `skip_ratio`,
`detectors_selected`, `verdicts_reached`, `seeded_selected`, `passed_on_acquired`,
`thresholds` (object), `breaches` (list), `not_applicable` (list), `tests` (object by node id).
No version field: a format change is visible only through the sample diff.

### Detector verdicts (`verdicts.jsonl`)

| Field | Type | Meaning |
|---|---|---|
| `run_id` | string | run the verdict belongs to |
| `nodeid` | string | detector test |
| `kind` | string | `expected_red` |
| `ticket`, `subject` | string | tracked defect |
| `verdict` | string | `reproduces`, `cleared`, `closed_pass`, `closed_regressed`, `held_empty`, `signature_mismatch` |
| `detail` | string | the failure text that matched |
| `timestamp` | ISO 8601 | when it was recorded |

### Case links (`case-links.json`)

`schema_version` (version field, currently 1), `run_id`, `generated_at`, `links` (list of
`{nodeid, case_id, requirements, origin}`), `invalid`, `mismatches`, `tests`.

### Failure bundle

`test`, `timestamp`, `errorType`, `error`, `stackTrace`, `screenshot`, `domSnapshot`,
`consoleErrors`, `pageErrors`, `failedRequests`, `context` (`runId`, `deployedVersion`).

### Flakiness history

One line per test execution: `test_id`, `run_id`, `outcome`, `duration_s`, `error`,
`timestamp`, `worker`, `environment`.

### Results bundle

One JSON record per line with `type` and `schema_version` (version field, currently 1):
`run` (environment, lane, host, git sha, CI provider), `case`, `revision`, `invocation`,
`result` (outcome, timings, failure fingerprint, markers), `verdict`, `run_end`.

## Gaps

Questions the current artefacts cannot answer, from this repository's own state:

* **No version field on the run-trust markers or the report results.** A consumer cannot
  tell which format it is reading; it would need a `schema_version` like the case links and
  results bundle carry.
* **Flakiness history is per machine.** `reports/flakiness/history.jsonl` is append-only on
  one runner and is not carried across CI runs; cross-run flakiness comes from the
  results-history branch instead. Merging the two would need a run id shared with the
  results store (the history uses its own compact stamp).
* **Detector status is human-only.** `verdict-status.md` is Markdown; anything automated
  re-derives it from `verdicts.jsonl`.
* **The failure bundle's stack trace is pytest-internal.** It is the framework's traceback,
  not the test's; a consumer that wants the assertion site has to read `error` and the JUnit
  XML instead.
* **No trend of run-trust verdicts.** Each lane's verdict is written per run, but nothing
  records them over time; the results bundle would need a `run_trust` record.
