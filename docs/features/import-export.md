# Import and export

Results move in and out of the store in open formats: JUnit XML in (from any framework
in any language), CSV and JSON out, plus tracker-shaped payloads in TestRail's and Xray's
import formats. The code lives in `results/io/`.

## JUnit XML import

**What it does.** `results.io.junit.import_junit(store, path)` turns a JUnit document
into a run: suites and test cases become cases and result rows.

| JUnit | Store |
|---|---|
| `<failure>` / `<error>` / `<skipped>` / none | `failed` / `error` / `skipped` / `passed` (pytest's `type="pytest.xfail"` skip is `xfailed`) |
| `time` | `duration_ms` |
| `classname` + `name` | the test id: pytest XML maps back to node ids (`tests/test_x.py::TestA::test_b[1]`), any other producer gives `<classname>::<name>` |
| failure `message` (else the first text line) | `message` |
| failure text + `system-out` + `system-err` | `longrepr` |
| `[[ATTACHMENT\|path]]` | an evidence link (kind from the file extension) |
| `<property name="requirement\|feature\|defect\|owner\|priority\|case_id">` | case links, owner, priority, identity pin |

This is the zero-code path for another framework: emit JUnit, then import it. Without
`--run-id`, the run id is derived from the file content, so importing the same file twice
adds nothing.

**XML safety.** A document with a `DOCTYPE` or `ENTITY` declaration is refused before it
is parsed (no entity expansion, no external entities). If `defusedxml` is installed it is
used as well. The AI log analyser reads JUnit through the same parser.

**Limit.** JUnit has no stable test ids: imported cases are keyed on classname plus
name, so a renamed test is a new case.

## CSV and JSON exports

| Export | Columns / shape |
|---|---|
| cases | `case_id, case_key, title, owner, priority, status, markers, links, steps, file_path, line, current_revision_id` — JSON per `results/schemas/cases-export-v1.schema.json` |
| results of a run | final attempt per test: `run_id, nodeid, case_id, outcome, attempts, duration_ms, started_at, finished_at, worker, browser, message, failure_fingerprint, skip_reason, defect_ref, revision_id` — JSON (with the run header) per `results-export-v1.schema.json` |

CSV is RFC 4180: fixed column order, `\r\n` line ends, minimal quoting, UTF-8.

## Tracker formatters (TestRail-style, Xray-style)

Formatters only: they produce the payload a tracker's importer accepts; nothing is sent.
`push(payload, dry_run=True)` reports what would be sent and never touches the network;
a real send raises `PushNotSupported`. The shapes follow the trackers' public import
formats and are **format compatible, not integration tested** against a live instance.

- **TestRail:** cases as a CSV for the CSV importer (Title, Section, Priority, Type,
  Preconditions, Steps, Expected Result, References); results as JSON for
  `add_results_for_cases` (`case_id`, `status_id` passed=1 / failed=5, `comment`,
  `elapsed`, `version`, `defects`). Skipped and not-run results are *omitted* (TestRail
  cannot set "untested" through results) and listed as omitted.
- **Xray:** results in the Xray JSON import format (`info`, `tests[]` with `testKey`,
  `status` PASSED/FAILED/TODO/ABORTED, `start`, `finish`, `comment`, `evidence`; local
  evidence files up to 1 MB are embedded, others listed in the comment); a JUnit flavour
  with `test_key` properties for Xray's JUnit endpoint; cases as CSV.

**Mapping.** A case's tracker id is a `case_links` row of kind `tms:testrail` or
`tms:xray`, loaded from a two-column CSV (`case_id,tracker_id` or
`case_key,tracker_id`). A result without a mapping is reported as unmapped, never
dropped silently.

## How to use

```bash
python -m results import junit build/test-results.xml --framework junit5 [--run-id ID]
python -m results export cases --format csv --out reports/cases.csv
python -m results export results --run-id "$TEST_RUN_ID" --format json
python -m results import mapping testrail-ids.csv --kind tms:testrail
python -m results export tms testrail --run-id "$TEST_RUN_ID" --out testrail.json
python -m results export tms xray --run-id "$TEST_RUN_ID" --push --dry-run
python -m results export tms xray --run-id "$TEST_RUN_ID" --what junit --out xray-junit.xml
python -m results export tms testrail --what cases --out testrail-cases.csv
```

## REST handlers

| Method | Path | Purpose |
|---|---|---|
| POST | `/imports/junit?framework=&run_id=` | body is the JUnit XML; returns the import report (run ids, counts, rejected test cases with reasons) |
| POST | `/imports/bundle` | bundle push for a run that is not open yet |
| GET | `/exports/cases.csv`, `/exports/cases.json` | case export |
| GET | `/exports/runs/{run_id}/results.csv`, `results.json` | results export |
| GET | `/exports/runs/{run_id}/testrail`, `/xray` | tracker payload plus `unmapped` and `omitted` |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RESULTS_ARTIFACT_ROOT` | current directory | where Xray evidence files are read from |
| `RESULTS_DB` | `reports/results/results.db` | the store |

Optional dependency: `defusedxml` (listed in `requirements-platform.txt`); without it the
DOCTYPE/ENTITY guard alone protects the parser.

## Example: a Go service pushing results

```bash
go test ./... -v 2>&1 | go-junit-report > report.xml
python -m results import junit report.xml --framework go --run-id "go-$BUILD_NUMBER"
python -m results compare "go-$BUILD_NUMBER"          # against the previous imported run
python -m results dashboard --out reports/dashboard/  # the run shows up like any other
```
