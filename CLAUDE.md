# Claude Code — wiki_project

## What this is
Playwright + PyTest UI automation framework for wiki/content flows.
Clean Page Object Model, JSON-based locator management, Docker-ready.

## Run commands
```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Offline suite — no browser, no network (what CI's offline job runs)
ruff check .
pytest tests/ -n auto

# Browser suite against the live site (--env is qa or staging, default qa)
pytest -m "smoke or api or contract" -n auto --env=qa
pytest -m regression -n auto   # CI runs this on main only

docker compose up
```

## Project structure
```
conftest.py          # root fixtures, CLI options, Playwright plugin
api/                 # API layer — direct HTTP calls
ui/                  # page objects, locators, flows, testdata
core/                # base page, config reader, logger, core/ai modules
config/              # environments.json (qa, staging), settings.py
reports/             # test artifacts and screenshots
tests/               # offline framework tests
pytest.ini           # markers and options
pyproject.toml       # project metadata + tool config
```

## Key design rules
- Page Object Model — UI interactions belong in `ui/`, not in test files
- Locators are JSON-managed — do not hardcode selectors in test code
- HTTP to the system under test goes through the clients in `api/clients/`
- Structured logging — use the framework logger, not `print()`
- Tests are independent — no shared mutable state between tests

## Coding standards
- PEP 8, 4-space indent, max 100 chars (`pyproject.toml`)
- Type hints everywhere
- `ruff check .` before commit — CI runs it

## CI gate
`tests/test_ci_selection.py` fails when any collected test is not selected by a pytest
command in `.github/workflows/tests.yml`. A new test directory or marker must be wired
into the workflow in the same change — never widen the gate to tolerate the gap.

## Constraints
- No hardcoded URLs — use config files
- Screenshots and artifacts go to `reports/` only

## AI Skills (Claude Code slash commands)

| Command | What it does |
|---------|-------------|
| `/analyze-trace` | Extracts actions and errors from a Playwright trace ZIP |
| `/analyze-test-failure` | Reads pytest JUnit XML and diagnoses failures |
| `/auto-run-fix` | Autonomous loop: run → analyze → fix → rerun until passing |
| `/generate-test-from-trace` | Generates a complete pytest test from a trace ZIP |
| `/generate-from-ts <file.ts>` | Converts a `playwright codegen` recording → Python Page Object + pytest test + locators + testdata; runs 3×, auto-fixes, reports push-ready |
| `/generate-from-postman <file.json>` | Converts a Postman collection → pytest API tests via existing `api/agents/` pipeline; falls back to manual generation |
| `/generate-from-swagger <url\|file>` | Reads OpenAPI spec → pytest API test classes (happy path + auth + errors); runs 3× |
| `/generate-from-prd <path\|url\|text>` | Reads PRD/user story → extracts scenarios → spec file + pytest tests; runs 3× |
| `/detect-coverage-gaps [spec\|url\|all]` | Compares OpenAPI/spec files vs existing tests; reports untested endpoints/scenarios; optionally invokes generate skills |
| `/test-health-report` | Runs full suite, parses JUnit XML + healing events, outputs pass/fail/healed summary with deploy-gate recommendation |
| `/create-pr [title] [branch]` | Pushes branch, opens GitHub PR via `gh` CLI with test health summary attached |
| `/graphify` | Builds or queries a knowledge graph of this codebase |

Skills live in `.claude/commands/`.

## Specs Directory

`specs/` holds hand-written, version-controlled test plans. `web_discovery` crawls a site and
builds scenarios in memory; it does not write to `specs/`.

| Component | Role |
|-----------|------|
| `TestGenerator.generate_from_spec(path)` (`core/ai/test_generator.py`) | Turns one spec into a pytest + Playwright test for `ui/tests/` |
| `autonomous_ui/healer.py` | Detects broken locators or regressions and applies targeted fixes |

### Spec file format

```
---
seed: true|false    # true = hand-written bootstrap spec
feature: <name>     # used as pytest marker
---

## Scenario <name>

### Preconditions
- State required before the test starts

### Steps
1. Numbered UI/API actions

### Expected
- Verifiable outcomes

### Tags
`marker1` `marker2`
```

`scripts/auto_runner.py` does not read specs; generate from a spec by calling
`generate_from_spec` directly.

## AI Modules (core/ai/)

| Module | Purpose |
|--------|---------|
| `TraceAnalyzer` | Parses Playwright trace ZIPs, calls Claude for analysis |
| `LogAnalyzer` | Reads pytest JUnit XML + logs, diagnoses failures |
| `AutoFixer` | Generates targeted code fixes and applies them |
| `TestGenerator` | Generates Page Objects + test files from traces or specs |

### Claude backend — API key vs CLI

- The four `core/ai/` modules call the Anthropic API directly and need `ANTHROPIC_API_KEY`;
  without it they return `None` or a descriptive message. They have no CLI fallback.
- `ClaudeLLMClient` (`api/llm/claude_client.py`, used by `autonomous_ui/`, `plugins/`,
  `orchestration/`, `core/agents/healer_agent.py`) uses the API key when set, else falls back to the `claude -p` CLI.

## Autonomous Run-Fix Loop

```bash
# Full autonomous loop (needs ANTHROPIC_API_KEY)
python scripts/auto_runner.py

# With test filter
python scripts/auto_runner.py -k "test_search" --max-iterations 3

# Analyze only, no code changes
python scripts/auto_runner.py --no-fix
```

## Accumulated Patterns

**Always read `docs/ai_learnings.md` before writing or fixing tests.** Key patterns:
- Wait for page: `page.wait_for_load_state("networkidle")` or `expect(locator).to_be_visible(timeout=N)`
- Stable selectors: `get_by_test_id` > `get_by_role` > `get_by_label` > `get_by_text`
- Never `time.sleep()` — use Playwright's built-in waits
- Re-resolve locators after page re-renders (never hold ElementHandle across dynamic updates)
- Traces are timestamped: `reports/traces/{test}_{browser}_{YYYYMMDD_HHMMSS}.zip`
