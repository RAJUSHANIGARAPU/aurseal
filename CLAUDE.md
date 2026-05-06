# Claude Code — wiki_project

## What this is
Playwright + PyTest UI automation framework for wiki/content flows.
Clean Page Object Model, JSON-based locator management, Docker-ready.

## Run commands
```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run all tests
pytest --env=development

# Run with Docker
docker compose up

# Run specific marker
pytest -m smoke
pytest -m regression
```

## Project structure
```
conftest.py          # root fixtures, CLI options, Playwright plugin
api/                 # API layer — direct HTTP calls
ui/                  # page objects, locators, flows
core/                # transport primitives, YAML helpers
config/              # environment config files
data/                # test data, payloads
reports/             # test artifacts and screenshots
pytest.ini           # markers and options
pyproject.toml       # project metadata + tool config
```

## Key design rules
- Page Object Model — UI interactions belong in `ui/`, not in test files
- Locators are JSON-managed — do not hardcode selectors in test code
- All network calls go through the transport layer in `core/`
- Structured logging — use the framework logger, not `print()`
- Tests are independent — no shared mutable state between tests

## Coding standards
- PEP 8, 4-space indent, max 120 chars
- Type hints everywhere
- `flake8` / `pylint` before commit

## Constraints
- No hardcoded URLs — use config files
- No `print()` in tests — use structured logger
- Screenshots and artifacts go to `reports/` only

## AI Skills (Claude Code slash commands)

| Command | What it does |
|---------|-------------|
| `/analyze-trace` | Extracts actions and errors from a Playwright trace ZIP |
| `/analyze-test-failure` | Reads pytest JUnit XML and diagnoses failures |
| `/auto-run-fix` | Autonomous loop: run → analyze → fix → rerun until passing |
| `/generate-test-from-trace` | Generates a complete pytest test from a trace ZIP |

Skills live in `.claude/commands/`. All use `ANTHROPIC_API_KEY` via `core/ai/` modules.

## Specs Directory

`specs/` contains human-readable test plans that are the output of the **Planner** stage in the
Planner → Generator → Healer workflow:

| Stage   | Component                    | Role |
|---------|------------------------------|------|
| Planner | `web_discovery`              | Crawls the target site and produces scenario descriptions; output is committed to `specs/` |
| Generator | `core/ai/TestGenerator`    | Reads spec files and generates pytest + Playwright test files under `ui/tests/` |
| Healer  | `autonomous_ui/healer.py`    | Detects broken locators or regressions and applies targeted fixes |

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

`TestGenerator` is invoked automatically by `python scripts/auto_runner.py` and reads specs as
context when generating or healing test files.

## AI Modules (core/ai/)

| Module | Purpose |
|--------|---------|
| `TraceAnalyzer` | Parses Playwright trace ZIPs, calls Claude for analysis |
| `LogAnalyzer` | Reads pytest JUnit XML + logs, diagnoses failures |
| `AutoFixer` | Generates targeted code fixes and applies them |
| `TestGenerator` | Generates Page Objects + test files from traces |

## Autonomous Run-Fix Loop

```bash
# Full autonomous loop (requires ANTHROPIC_API_KEY)
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
