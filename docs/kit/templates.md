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

## Available types

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

### `strategy`

A test strategy and test plan document set, with no code: quality goals, principles,
test levels and ownership, tooling, environments and test data, gates and metrics,
reporting. Its `make check` (also its CI) lints the Markdown and fails when a required
section is missing.

## Planned types

Listed by `testkeel list-types` as `planned`; `new-assignment` refuses them with exit
code 2.

| Type | Scope |
|---|---|
| `api-graphql` | GraphQL API tests |
| `perf-k6` | Load tests with k6 |
| `perf-locust` | Load tests with Locust |
| `a11y` | Accessibility checks with axe |
| `mobile` | Mobile app tests with Appium |
| `fullstack` | `web-ui` and `api-rest` combined |

## How the templates are kept honest

The package's own tests render every available type and check the result: the file tree
matches a committed snapshot, no template syntax survives, the code lints and type-checks,
and the `api-rest` project passes its own offline tests. The [examples](examples.md) were generated from these
templates; each carries a `.scaffold-manifest` listing its template-owned files, and a
test fails when a fresh render no longer matches them.
