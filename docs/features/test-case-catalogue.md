# Test case catalogue

The catalogue is generated from code: every collected pytest test becomes a case with a
stable id, a title, steps, an owner, a priority, markers and links. Nobody maintains a
second list next to the tests. The code lives in `results/catalogue.py`; cases are stored
in the results store (`cases`, `case_links`) and exported as `catalogue.json` and
`catalogue.md`.

## Case identity

**What it does.** A case is one test *function*: `case_key` is
`<path>::<Class>.<function>`, so a test with 50 parameter rows is one case (each result row
keeps its `param_id`). The derived id is `c-<sha1 of the key, 16 hex>`, so it can be
computed from any pytest node id (`results.catalogue.case_id_for_nodeid`).

A rename or move changes the key, and therefore the derived id. Where history must
survive a rename, pin the identity:

```python
@pytest.mark.case_id("TC-0042")
def test_refund_is_partial(): ...
```

Without a pin, a rename shows as one removed case plus one added case; the catalogue diff
makes that visible but does not prevent it.

## Fields

| Field | Source, in order of precedence |
|---|---|
| title | `@allure.title(...)`, else the first docstring line, else the humanised function name |
| description | the rest of the docstring |
| steps | a `Steps:` block of numbered lines in the docstring, else the literal strings of `with allure.step("...")` found by a static AST walk (a computed name shows as `<dynamic>`) |
| owner | `@pytest.mark.owner("team-a")`, else the `CODEOWNERS` rule matching the file (`CODEOWNERS`, `.github/CODEOWNERS` or `docs/CODEOWNERS`; the last matching rule wins, as on GitHub) |
| priority | `@pytest.mark.priority("P1")`, else the allure severity |
| markers | every marker name; arguments kept for `requirement`, `feature`, `known_defect`, `case_id`, `owner`, `priority`, `journey_group` |
| links | `requirement(...)`, `feature(...)` and allure `feature` labels, plus the `known_defect` id, stored in `case_links` (source `marker`) |

Building the catalogue never runs a test body: it is a `--collect-only` pass. Note that
collection *imports* test modules, so module-level code does run, exactly as in any
pytest collection.

The `case_id`, `owner`, `priority` and `feature` markers are registered by the results
plugin.

```python
@pytest.mark.owner("team-pay")
@pytest.mark.priority("P1")
@pytest.mark.requirement("REQ-12")
@pytest.mark.feature("billing")
def test_paying_marks_the_invoice_paid():
    """Paying an invoice marks it paid.

    Steps:
    1. Create an invoice
    2. Pay it
    3. Read it back
    """
```

## How cases reach the store

- **During a run.** Where tests run (each xdist worker, or the single process), the
  results plugin attaches the test's case to its setup report. The controller stamps
  each result row with `case_id` and upserts the case before writing the batch. A
  filtered run (`-k`, `-m`, a path, `--lf`) only refreshes `last_seen_at`: it never marks
  anything removed.
- **Catalogue build.** `python -m results catalogue build` collects the configured
  `testpaths` in a subprocess, writes `catalogue.json` (schema
  `results/schemas/catalogue-v1.schema.json`) and `catalogue.md` (grouped by file, then
  feature), and updates the store. Only a *complete* build (no selection arguments, no
  collection errors) marks absent cases `removed`.

## Command line

```bash
python -m results catalogue build                       # reports/catalogue/catalogue.{json,md}
python -m results catalogue build --out out/ --no-store
python -m results catalogue build --diff-against main-catalogue.json   # added/removed/renamed/changed
python -m results catalogue build -- -k billing          # partial: never removes cases
```

The exit status is 1 when collection reported errors.

## REST handlers

| Method | Path | Purpose |
|---|---|---|
| GET | `/cases?marker=&owner=&priority=&status=&requirement=&feature=` | filtered case list |
| GET | `/cases/{case_id}` | one case, with links and its current revision's steps |
| GET | `/catalogue` | the stored catalogue as catalogue JSON |

## Python interface

`case_from_item(item)`, `extract_steps(func, docstring)`, `build_catalogue(rootdir, args=())`,
`Catalogue.to_json()` / `.to_markdown()`, `diff_catalogues(old, new)` (added, removed,
renamed-by-pin, changed with the field names), `store_catalogue(store, catalogue)`,
`case_key_from_nodeid(nodeid)`, `case_id_for_nodeid(nodeid)`.

## Configuration

No environment variables of its own; it follows the results store (`RESULTS_DB`,
`RESULTS_STORE=off` also stops case stamping).

## Limits

Steps are only as good as the docstrings and literal `allure.step` calls; a test with
neither has a title and markers only.
