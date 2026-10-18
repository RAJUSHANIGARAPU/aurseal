# CLI reference

Taken from `testkeel --help` and each command's `--help` (version 0.1.0).
`tests/test_kit_docs.py` fails when an option here and the installed command disagree.

```text
Usage: testkeel [OPTIONS] COMMAND [ARGS]...
```

| Command | What it does |
|---|---|
| `testkeel version` | Print the installed version. |
| `testkeel list-types` | Assignment types: available now, or planned. |
| `testkeel new-assignment` | Render a new assignment project, leak-gate it, then write it. |
| `testkeel report` | Render a self-contained HTML summary of one or more result files. |
| `testkeel assignment check` | Run the leak gate over an existing project. |

Every command accepts `--help`.

## `testkeel version`

No options. Prints the version, for example `0.1.0`.

## `testkeel list-types`

| Option | Meaning |
|---|---|
| `--json` | Machine-readable output. |

Output today:

```text
web-ui       available Browser UI tests with Playwright: page objects, components, offline tier (needs browser)
api-rest     available REST API tests with httpx and pydantic: client, services, schema checks
strategy     available A test plan and strategy document set, no code
api-graphql  planned   GraphQL API tests
perf-k6      planned   Load tests with k6 (needs node)
perf-locust  planned   Load tests with Locust
a11y         planned   Accessibility checks with axe (needs browser)
mobile       planned   Mobile app tests with Appium (needs android-sdk)
fullstack    planned   web-ui and api-rest combined (needs browser)
```

## `testkeel new-assignment`

| Option | Value | Default | Meaning |
|---|---|---|---|
| `--type` | text | | See `list-types`. |
| `--name` | text | | Project name (free text, slugged). |
| `--company` | text | | Only rendered into the README. |
| `--task-file` | path | | Task brief, copied to `.task/`. |
| `--target-url` | text | | Web app under test. |
| `--api-base-url` | text | | API under test. |
| `--python` | text | `3.12` | 3.11, 3.12 or 3.13. |
| `--report` | text | `html` | html, allure or both. |
| `--ci` | text | `github` | github, gitlab or none. |
| `--docker` / `--no-docker` | flag | `--docker` | Dockerfile and compose file. |
| `--git` / `--no-git` | flag | `--git` | `git init`, no commit. |
| `--credit` / `--no-credit` | flag | `--no-credit` | One README footer line naming testkeel. |
| `--output-dir` | path | `./<slug>` | Where to write. |
| `--dry-run` / `--no-dry-run` | flag | `--no-dry-run` | List what would be written. |
| `--force` / `--no-force` | flag | `--no-force` | Write into a non-empty dir. |
| `--non-interactive` / `--no-non-interactive` | flag | `--no-non-interactive` | Never prompt. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Created. |
| 1 | Unexpected error. |
| 2 | Usage error, including a `planned` type. |
| 3 | Output directory not empty (use `--force`). |
| 4 | Leak gate failed; nothing was written. |
| 6 | Missing prerequisite. |
| 130 | Interrupted. |

## `testkeel report`

```text
Usage: testkeel report [OPTIONS] FILES...
```

`FILES`: one or more JUnit XML or Playwright JSON reports (required).

| Option | Value | Default | Meaning |
|---|---|---|---|
| `--output`, `-o` | path | `site` | Directory to write. |
| `--title` | text | `Test report` | Page title. |
| `--format` | text | detected | Skip format detection (`junit`, `playwright`). |

Exit codes: 0 written, 2 usage error, 3 unreadable input or zero tests found.

## `testkeel assignment check`

```text
Usage: testkeel assignment check [OPTIONS] [PATH]
```

`PATH`: project directory, default `.`.

| Option | Meaning |
|---|---|
| `--json` | Machine-readable findings. |
| `--strict` / `--no-strict` | A missing denylist fails (default `--no-strict`). |

Exit codes: 0 clean, 2 cannot run, 4 findings.

## The leak gate on its own

The gate also has a module entry point, used by this repository's CI:
`python -m testkeel.leakgate scan --help`. See [Leak gate](leak-gate.md).
