# Report metadata

Modules: `reporting/allure_environment.py`, `reporting/allure_categories.py`,
`reporting/step_screenshots.py`, `reporting/pytest_plugin.py`.

## Registering the plugin

`ReportMetadataPlugin` writes the session-level files once, from the xdist
**controller** only. If workers wrote them too, they would race to write the same
files. At configure time the plugin also pins the run id (see
[config-env.md](config-env.md)), so every worker tags its traffic with the same id.

```python
# conftest.py, inside pytest_configure(config)
from reporting.pytest_plugin import register as register_report_metadata

register_report_metadata(config)
```

Without the root `conftest.py`: `pytest -p reporting.pytest_plugin --alluredir=reports/allure-results`.
Nothing is written when `--alluredir` is not set.

## Allure environment and executor

### What it does

At session start, two files are written into `--alluredir`.

`environment.properties` (the Environment panel):

| Row | Source |
|---|---|
| `Environment` | `--env` |
| `Base.URL` | `ConfigReader(env).get("base_url")` |
| `Browser`, `Headless` | pytest-playwright `--browser`, `--headed` |
| `Playwright.Version`, `Python.Version`, `OS` | installed packages, runtime |
| `Auth.Mode` | the **resolved** mode (`resolve_auth_mode`), not the file value |
| `Git.Branch`, `Git.Commit` | `GITHUB_HEAD_REF`/`GITHUB_REF_NAME`/`GIT_BRANCH`/`BRANCH_NAME`, `GITHUB_SHA`/`GIT_COMMIT`, else `git` |
| `Run.Mode` | `CI` when `CI`, `GITHUB_ACTIONS`, `BUILD_NUMBER` or `JENKINS_URL` is set |
| `Run.Id`, `Run.Timestamp` | run id, UTC time |
| extra rows | `extra_rows` providers, for example checkout provenance |

`executor.json` (the Executor widget):

- GitHub Actions: a link to `<server>/<repo>/actions/runs/<id>`, named `<workflow> #<run number>`;
- Jenkins: `BUILD_URL`, named `<JOB_NAME> #<BUILD_NUMBER>`;
- otherwise `local @ <host>`.

Writing report metadata never stops a run. A failure is logged, and nothing is
written.

### Example

```python
from core.provenance import CheckoutProvenance
from reporting.pytest_plugin import register as register_report_metadata

specs = CheckoutProvenance(prefix="SPEC_REPO", default_sibling="shared-specs")
register_report_metadata(config, extra_rows=[lambda: specs.as_environment("Specs")])
```

## Allure categories

### What it does

`categories.json` sorts failures and skips by what they mean, so the first
triage question (whose problem is this?) is answered before anyone opens a test.
Allure assigns the **first** category whose status matches and whose
`messageRegex` matches the **whole** message, so the order sets the precedence.

| Category | Statuses | Typical message |
|---|---|---|
| Known defect no longer reproduces | failed | `[XPASS(strict)] known defect BUG-142 is open...` |
| Known defect still reproduces (expected red) | skipped | `XFAIL known defect BUG-142 is open...` |
| Backend 5xx / transient network | failed, broken | `HTTP 503`, `Bad Gateway`, `net::ERR_...`, connection reset |
| Authentication | failed, broken | `401`, `403`, `Unauthorized` |
| Configuration / setup | failed, broken | `Missing config key`, `Unknown env`, `fixture ... not found` |
| Broken locators | failed, broken | `strict mode violation`, `resolved to 0 elements` |
| Timeouts | failed, broken | `Timeout 30000ms exceeded`, `TimeoutError` |
| Skipped: known product bug | skipped | `known product bug`, `blocked by BUG-77` |
| Skipped: environment limitation | skipped | `no test data`, `not available on this environment` |
| Skipped: superseded or duplicate coverage | skipped | `superseded by ...` |
| Skipped: harness / on-demand | skipped | `on-demand`, `opt-in`, `ENABLE_...` |
| Test or harness errors | broken | any other non-assertion exception |
| Product defects | failed | any other assertion failure |

To have a skip land in the right bucket, word the skip reason accordingly, for
example `pytest.skip("blocked by BUG-77: export drops the header row")`.

### How to use it

The plugin writes the file automatically. To write it yourself, or to check where
a message would land:

```python
from reporting.allure_categories import categorize, write_categories

write_categories("reports/allure-results")
categorize("failed", "TimeoutError: Locator.click: Timeout 30000ms exceeded.")  # "Timeouts"
```

A custom list can be passed as the second argument. A pattern that does not
compile fails when the file is written, not later in the report.

## Per-step screenshots

### What it does

With `ALLURE_STEP_SCREENSHOTS=true`, each `allure.step` gets a viewport PNG of
the page taken as the step **ends**. The image is attached while the step is
still open, so in the report it sits inside the step it shows. The listener hooks
Allure's own `stop_step` with `tryfirst`, so it runs before allure-pytest closes
the step. A failed capture is logged at DEBUG and never affects the step. Tests
that never set a page (API-only tests) are not touched.

### How to use it

The reporting plugin registers the listener when the flag is on. Tell it which
page to capture:

```python
from reporting.step_screenshots import set_current_page


@pytest.fixture(autouse=True)
def step_screenshot_page(page):
    set_current_page(page)
    yield
    set_current_page(None)
```

```python
import allure

def test_checkout(page):
    with allure.step("Open the basket"):
        page.goto("https://shop.example.test/basket")
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ALLURE_STEP_SCREENSHOTS` | off | `true` attaches a screenshot to every step |
