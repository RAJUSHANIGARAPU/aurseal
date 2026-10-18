# Importers

An importer reads one report file into the result model (`aurseal.model.v1`): one
record per test, configuration and parameter variant, with retries kept as attempts of
that record. `aurseal report` picks the importer by sniffing the start of each file;
`--format NAME` skips the sniffing.

| Name | Reads | Produce it with |
|---|---|---|
| `junit` | JUnit XML (pytest, Maven Surefire and compatible reporters) | `pytest --junitxml=junit.xml`, Surefire's default reports |
| `playwright` | Playwright's JSON reporter | `reporter: [["json", { outputFile: "results.json" }]]` |
| `aurseal` | The canonical upload document (`RunUpload` JSON) | the [pytest plugin](#pytest-plugin) |
| `cypress` | mochawesome JSON (one spec or a `mochawesome-merge` report); the Cypress Module API results | `--reporter mochawesome --reporter-options json=true`; `JSON.stringify(await cypress.run())` |
| `newman` | Newman's `json` reporter (the run summary) | `newman run collection.json --reporters json --reporter-json-export newman.json` |
| `k6` | k6's end-of-test summary: `handleSummary` data or `--summary-export` | `handleSummary(data) { return { "k6-summary.json": JSON.stringify(data) }; }` |

## Status mapping

Every importer maps its format onto the same statuses: `passed`, `failed` (an assertion
or a timeout), `broken` (an error outside the assertion: setup, teardown, interrupted),
`skipped`. A `reason` keeps what the status alone loses: `timeout`, `xfail`, `xpass`,
`xpass_strict`, `expected_failure`, `not_run`.

A record's status is its last attempt's status. A record that failed and then passed on a
retry is `passed` with `flaky_in_run` set.

## Cypress

Both inputs give a test the same identity: the spec file is the path, the `describe`
titles are the suite, the `it` title is the name.

- **Module API results** (`cypress.run()`): every entry in `attempts` is an attempt, and
  the browser is the configuration.
- **mochawesome** keeps only the final attempt and a retry count. Mocha retries a test
  only after it failed, so the earlier attempts are recorded as failed attempts without
  detail. A failed hook is a `broken` record named after the hook; the tests it stopped
  are `skipped` with reason `not_run`. mochawesome does not name the browser.

## Newman

One record per request and iteration: the collection name is the path, the folders above
the request are the suite, the request name is the name, and the environment name is the
configuration. With a data file (more than one iteration) the iteration is the variant,
so one request is one case with a result per data row.

A request error or a script error is `broken`, a failed assertion `failed` (the message
names the assertion), a request whose assertions were all skipped `skipped`. A request
sent again within one iteration (`setNextRequest`) is another attempt of the same record.

## k6

Every threshold is a record: the metric is the suite, the threshold expression the name,
and the status `passed` or `failed`. A run without thresholds has no verdict and imports
no records. The summary does not name the script, so keep each script's summaries in a
project of their own.

Each record carries a `perf.k6` result type payload (schema `perf.k6/1`): the metric's
type and values (p95, p99, rates, counts), the threshold, its outcome and the value it
judged. The result type is a plugin like any other (entry point group
`aurseal.result_types`), so its JSON Schema is available to anything that discovers it.

`--summary-export` writes thresholds as booleans that are true when the threshold
failed; the importer reads them that way. k6's newer machine-readable summary
(`--new-machine-readable-summary`) holds no threshold outcomes and is refused with a
message saying so.

## pytest plugin

Installing `aurseal` installs a pytest plugin. It does nothing until you ask for the
file:

```bash
pytest --aurseal-out=reports/aurseal.json
aurseal report reports/aurseal.json
```

What it records, compared with `--junitxml`:

- **Identity from the node id.** `tests/shop/test_cart.py::TestCart::test_total[EUR-10]`
  is path `tests/shop/test_cart.py`, suite `TestCart`, name `test_total`, variant
  `EUR-10`: the same identity the JUnit importer reconstructs, without the guesswork.
- **Explicit case ids.** `@pytest.mark.case("SHOP-7")` (or `case_id`), or
  `record_property("case_id", "SHOP-7")`.
- **Retries.** With pytest-rerunfailures each rerun is an attempt of one record.
- **xfail and xpass.** `xfail` is skipped with reason `xfail`; an `xpass` passes with
  reason `xpass`; a strict `xpass` fails with reason `xpass_strict`.
- **Setup and teardown errors** are `broken`, call failures `failed`.

Under pytest-xdist the controller writes the file; workers write nothing. Turn the plugin
off entirely with `-p no:aurseal`.

## Writing an importer

An importer is a class with a `name`, `sniff(head: bytes) -> float` (confidence that a
file starting with `head` is its format) and `parse(stream) -> Iterator[TestRecord]`.
Register it in your package's `pyproject.toml`:

```toml
[project.entry-points."aurseal.importers"]
myformat = "my_package.importer:MyImporter"
```

A plugin with the name of a built-in replaces it.
