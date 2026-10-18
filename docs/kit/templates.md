# Templates

`testkeel new-assignment --type <type>` renders a [Copier](https://copier.readthedocs.io/)
template that ships inside the package. Every type is a common skeleton plus a
type-specific overlay. Templates use no Copier tasks and no Jinja extensions, so rendering
never runs code from the template.

## What every project gets

| File | Purpose |
|---|---|
| `README.md` | How to run it, a decisions and trade-offs table to confirm, scope, results |
| `TEST_PLAN.md` | Scope, risks and priority, automated vs exploratory, entry and exit criteria, environments and data, known limits |
| `pyproject.toml`, `.python-version` | uv-managed project; ruff, strict mypy on `src/`, pytest with xdist and pytest-html |
| `Makefile` | `install`, `lint`, `typecheck`, `test-offline`, `test`, `report`, `check`, ... (`make help`) |
| `Dockerfile`, `docker-compose.yml` | Run the suite with only Docker installed (`--no-docker` leaves them out) |
| `.github/workflows/ci.yml` | ruff and mypy, then the tests, then the report; on `main` the report is published to GitHub Pages (`--ci none` leaves the workflow out) |
| `.pre-commit-config.yaml`, `.editorconfig` | Local hygiene |
| `.env.example` | Every setting the suite reads, with safe defaults |
| `tests/unit/` | Offline tests that pass on the first run |

The `strategy` type is documents only: it gets the README, the test plan and its own CI,
but no Python project, Dockerfile or `.env.example`.

## Types

### `web-ui`

Browser tests with Playwright for Python.

- `src/<package>/pages/`: a base page, a login page and a home page (page objects).
- `src/<package>/components/`: reusable parts shared by pages, such as navigation.
- `src/<package>/data/`: sign-in cases, one row per equivalence class.
- `tests/ui/`: the live suite against `--target-url`.
- `tests/unit/` with `tests/fixtures/static/`: page-object tests against a static HTML
  fixture, so the offline tier runs without the target being up.

Needs a browser: `make install` downloads the Chromium build the pinned Playwright
version expects.

### `api-rest`

REST API tests with httpx and pydantic.

- `src/<package>/client/`: one HTTP client with timeouts and retries in one place.
- `src/<package>/services/`: one class per resource, returning typed models.
- `src/<package>/models/` and `schemas/`: pydantic models and JSON Schemas for responses.
- `tests/api/`: the live suite against `--api-base-url`.
- `tests/unit/`: client, service and model tests that need no network.

### `api-graphql`

GraphQL API tests with gql over httpx.

- `schema.graphql`: a snapshot of the schema; operations are validated against it offline.
- `src/<package>/operations/`: one `.graphql` file per operation, and the loader that
  validates them.
- `src/<package>/client/`: timeouts and redacted logging; a response's `errors[]` is raised
  as one exception type.
- `src/<package>/services/` and `models/`: one class per resource, returning pydantic models.
- `src/<package>/contract.py`: introspection and a snapshot diff; `--record` refreshes
  `schema.graphql`.
- `tests/api/`: the live suite against `--api-base-url`; `tests/unit/`: offline tests on
  `httpx.MockTransport` and the snapshot.

### `a11y`

Accessibility checks with axe-core, driven by Playwright.

- `src/<package>/a11y/scanner.py`: injects axe-core and runs it with the configured WCAG
  tags (WCAG 2.1 AA by default).
- `src/<package>/a11y/baseline.py` and `a11y-baseline.json`: known violations, each with its
  issue. The verdict is new, still known or fixed; entries may only be removed.
- `src/<package>/a11y/pages.py`: the pages and page states the suite scans.
- `tests/a11y/`: live scans of `--target-url`.
- `tests/unit/` with `tests/fixtures/static/`: a page with planted violations and its fixed
  version. The planted page is the positive control: the offline tier fails if the scanner
  stops finding them.

Needs a browser, like `web-ui`.

### `strategy`

A test strategy and test plan document set, with no code: quality goals, principles,
test levels and ownership, tooling, environments and test data, gates and metrics,
reporting. Its `make check` (also its CI) lints the Markdown and fails when a required
section is missing.

### `perf-k6`

Load tests with [k6](https://k6.io/); Python only checks the configuration.

- `k6/scenarios/`: `smoke.js`, `load.js` and `stress.js`, one profile per file.
- `k6/slo.json`: the SLO budgets; `k6/thresholds.js` turns them into k6 thresholds, so a
  breached budget fails the run.
- `k6/lib/`: configuration, the target guard, checks, test data and the HTML summary.
- `src/<package>/guard.py`: `make load` and `make stress` refuse a target that is not local.
  CI only ever runs the smoke profile.
- `tests/unit/`: offline tests on the SLO file, the guard and the settings.

Needs the `k6` binary (a Go program, not a Python or Node package). The Docker image and
the CI workflow pin the same k6 version.

### `perf-locust`

Load tests with [Locust](https://locust.io/), held to SLO budgets after the run.

- `locustfiles/` and `profiles/`: entry points and one Locust config per profile (smoke,
  load, stress).
- `src/<package>/users.py`: the simulated users (task sets, weights, pauses).
- `src/<package>/shapes.py`: the stepped ramp the stress profile uses.
- `src/<package>/slo.py` and `slo.json`: reads Locust's CSV statistics and fails on a
  breached budget.
- `src/<package>/guard.py`: the same target guard as `perf-k6`; CI runs only the smoke
  profile.
- `tests/unit/`: offline tests on the SLO check, users, shape, guard and settings.

### `mobile`

Android app tests with Appium 3 and the UiAutomator2 driver.

- `src/<package>/driver/`: capabilities from settings and the driver factory.
- `src/<package>/screens/`: one class per screen; methods are user actions.
- `apps/`: where `make app` downloads the demo app (pinned by release and SHA-256, never
  committed).
- `tests/mobile/`: live tests. They need an Appium server and an emulator or device, and
  skip themselves until `APP_APPIUM_URL` is set.
- `tests/unit/`: offline tests on the capabilities and on the screen objects against a fake
  driver.

Takes no URL option. The live tier needs the Android SDK, Java and Node (for Appium);
`make doctor` checks them. The offline tier needs none of them.

### `fullstack`

`web-ui` and `api-rest` in one project, rendered from both overlays and then its own.

- Everything from `web-ui` (pages, components) and from `api-rest` (client, services,
  models, schemas).
- `src/<package>/data/`: test data builders shared by both layers.
- `tests/ui/`: the live browser suite, including a test that creates data through the API
  and checks it in the UI.
- `tests/api/`: the live API suite; `tests/unit/`: offline tests for both layers.

Needs both `--target-url` and `--api-base-url`: no public demo serves a UI and its API
together, so there is no default. Needs a browser, like `web-ui`.

## Which URL each type takes

| Type | `--target-url` | `--api-base-url` | Default target |
|---|---|---|---|
| `web-ui` | yes | | `https://www.saucedemo.com` |
| `api-rest` | | yes | `https://restful-booker.herokuapp.com` |
| `api-graphql` | | yes | `https://countries.trevorblades.com` |
| `a11y` | yes | | `https://www.w3.org/WAI/demos/bad` |
| `perf-k6`, `perf-locust` | yes | | `https://quickpizza.grafana.com` |
| `fullstack` | required | required | none |
| `mobile`, `strategy` | | | none |

Passing a URL option the type does not take is a usage error (exit code 2), so a URL
never widens the leak gate's list of allowed hosts without reaching the project.

## How the templates are kept honest

The package's own tests render every type and check the result: the file tree matches a
committed snapshot, no template syntax survives, the code lints and type-checks, every
code type's project passes its own offline tests, and every type passes the leak gate's
[project rules](leak-gate.md#project-rules). The [examples](examples.md) were generated
from these templates; each carries a `.scaffold-manifest` listing its template-owned
files, and a test fails when a fresh render no longer matches them.
