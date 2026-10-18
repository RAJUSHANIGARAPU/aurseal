# wiki_project — Universal Test Automation Framework

A plug-and-play, AI-powered test automation framework for any web application. Clone it, point it at a URL, run. No org-specific assumptions, no proprietary dependencies.

Python + Pytest + Playwright for UI. Multi-agent pipeline for API. Self-healing. Failure analysis. Autonomous fix loops — all built in.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Core Capabilities](#3-core-capabilities)
4. [Project Structure](#4-project-structure)
5. [Installation](#5-installation)
6. [Running Tests](#6-running-tests)
7. [Multi-Agent API Testing](#7-multi-agent-api-testing)
8. [Autonomous UI Intelligence Layer](#8-autonomous-ui-intelligence-layer)
9. [Flakiness Intelligence System](#9-flakiness-intelligence-system)
10. [AI Skills (Slash Commands)](#10-ai-skills-slash-commands)
11. [Failure Evidence Bundles](#11-failure-evidence-bundles)
12. [Markers & Segmentation](#12-markers--segmentation)
13. [Docker Execution](#13-docker-execution)
14. [CI Integration](#14-ci-integration)
15. [Code Quality](#15-code-quality)
16. [Configuration & Environments](#16-configuration--environments)

---

## 1. Overview

This framework covers the full automation lifecycle end-to-end:

- **UI tests** via Playwright — page objects, locators, flows, traces, video
- **API tests** via a six-agent autonomous pipeline — ingest, generate, execute, analyse, heal, repeat
- **AI self-healing** — broken locators are detected, repaired by Claude, and persisted without human intervention
- **Failure bundles** — every test failure writes a structured JSON evidence file with screenshot, stack trace, DOM snapshot, console errors, and failed HTTP requests
- **Autonomous UI intelligence** — every failure is classified (LOCATOR / TIMEOUT / ASSERTION / NAVIGATION), healed automatically, and loop-guarded to prevent infinite patching
- **Flakiness intelligence** — every test run is tracked in an append-only JSONL store; flaky tests are classified by pattern (TIMING / ORDER_DEPENDENT / RESOURCE_CONTENTION / DATA_POLLUTION / ENVIRONMENT) and receive actionable, LLM-enriched fix recommendations
- **Autonomous fix loops** — run → analyse → patch → rerun until green

---

## 2. Architecture

### UI Layer

```
Tests → Flows → Pages → BasePage → Locators (JSON) → Playwright
                                 → AI Self-Healing (core/ai/)
```

### Autonomous UI Intelligence Layer

```
conftest.py (pytest hooks)
  ↓ failure bundle written on every test failure
UIOrchestrator
  ├── FailureAnalyzer    — rule-based classification → LLM fallback
  ├── UIHealer           — locator patch | wait_retry | assertion_patch
  └── Session log (JSONL) — structured healing events
```

### Flakiness Intelligence System

```
FlakinessPlugin (pytest_runtest_logreport)
  ↓ append-only JSONL per run
HistoryStore → FlakinessDetector → PatternAnalyzer → FlakinessRemediator
                                                            ↓
                                                      FlakinessReporter
                                                  (Markdown + JSON report)
```

### API Agent Pipeline

```
Orchestrator
  ├── IngestionAgent   — reads Postman collections / OpenAPI specs
  ├── GenerationAgent  — produces parameterised test cases
  ├── ExecutionAgent   — runs requests, captures responses
  ├── AnalysisAgent    — diagnoses failures, proposes fixes
  └── SelfHealingAgent — patches broken assertions, reruns
```

Supporting engines: DynamicDataEngine, ValidationEngine, ContextMemory, AgentLogger, LLM abstraction (Claude).

---

## 3. Core Capabilities

**UI**
- Page Object Model with JSON-based locator registry
- Multi-browser: Chromium, Firefox, WebKit
- Headed and headless modes; parallel execution
- Screenshot, video, and Playwright trace on every test
- Structured failure evidence bundle (JSON) on failure
- AI self-healing for broken locators
- Auto-fix loop: run → diagnose → patch → rerun

**API**
- Postman collection ingestion (v2.1)
- Dynamic data generation: Faker, env vars, `{{variable}}` templates
- Context memory — extract response values and reuse across requests
- Validation: status code, response time, JSON schema, headers
- Six-agent autonomous loop with LLM-powered analysis and healing
- Structured JSONL observability traces per session
- Claude-backed LLM layer (swappable via `BaseLLMClient` ABC)

**Autonomous UI Intelligence**
- Failure classification: LOCATOR / TIMEOUT / ASSERTION / NAVIGATION (rule-based, LLM fallback)
- Locator healing: reads DOM snapshot, asks Claude for an alternative selector, patches `wiki_locators.json`
- Wait healing: records retry config to `reports/healing_overrides.json` for next run
- Assertion healing: only when confidence is high — prevents masking real bugs
- Loop guard: each locator key is patched at most once per session
- CLI: `python -m autonomous_ui.orchestrator --path ui/tests/ --max-iterations 3`

**Flakiness Intelligence**
- Zero-latency JSONL tracking via `FlakinessPlugin` (fire-and-forget, parallel-safe)
- Per-test profile: flakiness rate, confidence, max consecutive failures, most common error
- Pattern classification: TIMING → ORDER_DEPENDENT → RESOURCE_CONTENTION → DATA_POLLUTION → ENVIRONMENT → LLM → UNKNOWN
- Targeted remediation: timing gap stats, worker breakdown, env breakdown — each with LLM-enriched, file-specific guidance
- Report output: `reports/flakiness/report-<run_id>.{md,json}` printed at session end

**Verification Oracles** (`verification/`, stdlib only)
- Twin equivalence: compare two paths' results with ignore-paths, numeric tolerance and date-shift rules; diff table of path / left / right / rule — `assert_equivalent(a, b, EquivalenceOracle(ignore=["**.id"]))`
- Invariants: declarative before/after rules (`advanced_by`, `scaled_by`, `unchanged`, `non_empty`, `no_item_in_state`, `custom`), composable with `+`, all violations reported at once
- Batch truthfulness: `verify_batch` re-reads item state after a run and flags FALSE_SUCCESS, FALSE_FAILURE (timeout after commit), FALSE_SKIP, DOUBLE_PROCESSED, MISCLASSIFIED, UNREPORTED
- Idempotency: `probe` / `probe_async` submit an operation twice or more (sequential, threads behind a barrier, asyncio) and assert exactly-once effect
- Expected-red: `@pytest.mark.known_defect("ID", raises=...)` — strict xfail with a defect reference; an unexpected pass fails and names the defect to close; `KNOWN_DEFECTS_CLOSED=ID,...` turns it off

**Infrastructure**
- Environment-aware config (`--env qa/staging/prod`)
- Docker images for both UI and API agent execution
- GitHub Actions CI with artifact upload
- Ruff linting + pre-commit hooks enforced

---

## 4. Project Structure

```
wiki_project/
├── api/
│   ├── agents/          — ingestion, generation, execution, analysis, healing, orchestrator
│   ├── clients/         — low-level HTTP clients
│   ├── engine/          — dynamic_data, context_memory, validation, observability
│   ├── llm/             — BaseLLMClient ABC + ClaudeLLMClient
│   ├── postman/         — sample Postman collection
│   └── tests/           — API-layer tests
├── autonomous_ui/
│   ├── analyzer.py      — failure classification (rule-based + LLM fallback)
│   ├── healer.py        — locator / wait / assertion healing strategies
│   ├── models.py        — FailureBundle, FailureAnalysis, HealingResult dataclasses
│   ├── orchestrator.py  — autonomous run → analyse → heal → rerun loop
│   └── flakiness/
│       ├── models.py        — FlakRecord, FlakinessProfile, RemediationResult, FlakPattern
│       ├── history_store.py — append-only JSONL store (parallel-safe)
│       ├── detector.py      — per-test profile computation + flaky/stable classification
│       ├── pattern_analyzer.py — TIMING/ORDER_DEPENDENT/RESOURCE_CONTENTION/DATA_POLLUTION/ENVIRONMENT
│       ├── remediator.py    — rule-based suggestions enriched by LLM
│       ├── reporter.py      — Markdown + JSON report generation
│       └── pytest_plugin.py — FlakinessPlugin (auto-registered via conftest.py)
├── core/
│   ├── ai/              — self-healing engine (Claude API)
│   ├── base_page.py
│   ├── config_reader.py
│   ├── failure_reporter.py
│   └── logger.py
├── verification/        — equivalence oracle, invariants, batch + idempotency probes, known_defect marker
├── ui/
│   ├── pages/
│   ├── flows/
│   ├── locators/        — JSON locator registries per page
│   ├── testdata/
│   └── tests/
├── tests/
│   ├── api_agent/       — unit tests for all agent and engine components
│   ├── autonomous_ui/   — 49 tests for analyzer, healer, orchestrator
│   └── flakiness/       — 50 tests for history store, detector, pattern analyzer, reporter
├── config/
│   └── environments.json
├── generated_tests/     — output directory for agent-generated test files
├── reports/
│   ├── failures/        — JSON evidence bundles (screenshot, DOM, console, network)
│   ├── flakiness/       — Markdown + JSON flakiness reports per run
│   ├── screenshots/
│   ├── traces/
│   └── videos/
├── scripts/
│   └── auto_runner.py   — autonomous run → fix → rerun loop
├── main.py              — CLI entry point for the API agent pipeline
├── Dockerfile
├── Dockerfile.api-agent
├── docker-compose.yml
├── pytest.ini
└── pyproject.toml
```

---

## 5. Installation

```bash
git clone <repository-url>
cd wiki_project
pip install -r requirements.txt
playwright install --with-deps
```

Set the Claude API key for AI features (degrades gracefully without it):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## 6. Running Tests

```bash
# UI tests
pytest ui/tests/ --browser chromium

# Headed mode
pytest ui/tests/ --headed --browser chromium

# Parallel
pytest ui/tests/ -n 4 --browser chromium

# API layer tests
pytest api/tests/

# Agent unit tests
pytest tests/api_agent/

# Specific environment
pytest --env staging
```

---

## 7. Multi-Agent API Testing

The agent pipeline reads a Postman collection, generates test cases, executes them, analyses failures, and heals broken assertions — with no manual intervention.

### Run via CLI

```bash
# Full autonomous loop against a Postman collection
python main.py run --collection api/postman/sample_collection.json --env qa

# Single-pass execution (no healing loop)
python main.py run --collection api/postman/sample_collection.json --no-heal

# Dry run — generate test cases only, do not execute
python main.py generate --collection api/postman/sample_collection.json
```

### Run via Docker

```bash
docker build -f Dockerfile.api-agent -t wiki-api-agent .
docker run -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY wiki-api-agent \
    run --collection api/postman/sample_collection.json
```

### Dynamic Data

Use `{{variable}}` in collection request bodies or URLs. Resolved in order:

1. ContextMemory (values extracted from prior responses)
2. Environment variables
3. Faker — `{{faker.name}}`, `{{faker.email}}`, `{{faker.uuid4}}`, etc.

### Observability

Every agent session writes a JSONL trace to `reports/agent-sessions/`. Each line is a structured event:

```json
{"ts": "2026-04-25T10:00:00Z", "agent": "execution", "event": "request_sent", "data": {...}}
```

---

## 8. Autonomous UI Intelligence Layer

The `autonomous_ui/` layer wraps every pytest UI run with failure analysis and automatic healing. It integrates purely via `conftest.py` hooks — no page objects or test files are touched.

### How it works

1. On failure, `conftest.py` captures a DOM snapshot and writes a JSON failure bundle to `reports/failures/`.
2. `FailureAnalyzer` classifies the failure: LOCATOR, TIMEOUT, ASSERTION, NAVIGATION, or UNKNOWN — using regex heuristics first, Claude as a fallback.
3. `UIHealer` applies the appropriate strategy:
   - **LOCATOR / TIMEOUT** — reads `ui/locators/wiki_locators.json`, asks Claude to suggest an alternative selector from the DOM snapshot, writes the patch back.
   - **WAIT_RETRY** — records a retry config to `reports/healing_overrides.json` when the locator registry is missing.
   - **ASSERTION** — delegates to `AutoFixer` only when confidence is `high`.
4. Every decision is written as a JSONL line to `reports/ui_healing_sessions.jsonl`.
5. A loop guard prevents the same locator key from being patched more than once per session.

### Run the autonomous loop

```bash
# Run → analyse → heal → rerun (up to 3 iterations)
python -m autonomous_ui.orchestrator --path ui/tests/ --max-iterations 3

# Dry run — classify failures without applying patches
python -m autonomous_ui.orchestrator --path ui/tests/ --analyze-only
```

### Healing overrides

If `reports/healing_overrides.json` lists tests that need reruns, the orchestrator automatically appends `--reruns N` to the next pytest invocation.

---

## 9. Flakiness Intelligence System

The flakiness system tracks every test execution and surfaces tests that intermittently fail — automatically, with targeted fix guidance.

### Plugin registration

`FlakinessPlugin` is auto-registered in `pytest_configure` (in `conftest.py`). It adds zero latency — the JSONL write is fire-and-forget.

### What gets tracked

Every test execution at the `call` phase writes a `FlakRecord` to `reports/flakiness/history.jsonl`:

```json
{"test_id": "ui/tests/test_search.py::test_search_train", "run_id": "20260425T143022Z",
 "outcome": "failed", "duration_s": 4.2, "error": "TimeoutError: 30000ms",
 "timestamp": "2026-04-25T14:30:22Z", "worker": "gw0", "environment": "qa"}
```

### Session report

At the end of every session with enough history (≥ 5 runs per test), a report is written to `reports/flakiness/`:

```
[flakiness] 2 flaky test(s) detected. Report: reports/flakiness/report-20260425T143022Z.md
```

The Markdown report includes a severity table, per-test pattern classification, and LLM-enriched fix recommendations. A JSON equivalent is written alongside it.

### Pattern classification (priority order)

| Pattern | Signal |
|---------|--------|
| ENVIRONMENT | Failures correlated with external service errors |
| TIMING | Timeout errors or wait-related keywords |
| RESOURCE_CONTENTION | Failure rate ≥ 3× higher on parallel workers vs sequential |
| DATA_POLLUTION | Shared data mutation keywords in errors |
| ORDER_DEPENDENT | LLM classification (single-word prompt, max_tokens=20) |
| UNKNOWN | No pattern identified |

### Flakiness thresholds

| Constant | Value | Meaning |
|----------|-------|---------|
| `MIN_RUNS` | 5 | Minimum runs before a judgement is made |
| `FLAKY_MIN_RATE` | 2% | Below this: statistical noise |
| `ALWAYS_FAIL_THRESHOLD` | 95% | Above this: broken test, not flaky |

---

## 10. AI Skills (Slash Commands)

Use these from the repo root inside Claude Code:

| Command | What it does |
|---------|-------------|
| `/analyze-trace` | Extracts actions, errors, and DOM state from a Playwright trace ZIP |
| `/analyze-test-failure` | Reads failure bundles and diagnoses root cause with fix proposals |

Skills are in `.claude/commands/`.

---

## 11. Failure Evidence Bundles

Every failing test writes a JSON bundle to `reports/failures/<test>-<timestamp>.json`:

```json
{
  "test": "test_login_flow",
  "timestamp": "2026-04-25T09:31:00Z",
  "error": "AssertionError: expected 'Dashboard' in page title",
  "stackTrace": "...",
  "screenshot": "<base64 PNG>",
  "domSnapshot": "<full page HTML>",
  "consoleErrors": ["TypeError: Cannot read properties of null"],
  "failedRequests": ["POST /api/auth/login → 401"]
}
```

The bundle is written by `core/failure_reporter.py` via the `pytest_runtest_makereport` hook in `conftest.py`. No test code changes required.

---

## 12. Markers & Segmentation

```bash
pytest -m smoke
pytest -m regression
pytest -m e2e
pytest -m negative
pytest -m api
pytest -m contract
```

`known_defect("ID", reason=..., raises=...)` marks an expected-red test for an open defect (see Verification Oracles).

---

## 13. Docker Execution

`docker compose` runs the suite in the same pinned Playwright image CI uses
(`Dockerfile`, tag equal to the `playwright==` pin in `requirements.txt`) and writes
results to the host's `reports/`. No Python or browsers needed on the host.

```bash
docker compose build
docker compose run --rm tests                        # offline suite + demo browser examples
docker compose run --rm tests tests/gate -q          # any pytest arguments
BROWSER=firefox docker compose run --rm tests tests/demo_examples -m demo_ui --browser firefox

# Live report from reports/allure-results on http://localhost:5050 (refreshes every 5 s)
docker compose --profile report up report

# Demo examples against a containerised demo service instead of the in-process fixture
DEMO_SUT_URL=http://demo:8765 docker compose --profile demo run --rm tests tests/demo_examples -m demo_ui
```

| Variable | Default | Meaning |
|---|---|---|
| `TEST_ENV` | `qa` | target environment |
| `BROWSER` | `chromium` | browser engine |
| `ANTHROPIC_API_KEY` | empty | model key; features that need it degrade without it |
| `REPORT_PORT` | `5050` | host port of the report server |
| `REPORT_HISTORY` | `20` | runs of history the report keeps |
| `DEMO_SUT_URL` | empty | demo base URL (`http://demo:8765` with the `demo` profile) |

Secret-like values come from the host environment or an ignored `.env` file, never from
the compose file. The report server image is pinned to an exact version.

### API Agent

```bash
docker build -f Dockerfile.api-agent -t api-agent .
docker run --rm -e ANTHROPIC_API_KEY api-agent
```

---

## 14. CI Integration

| Workflow | Runs |
|---|---|
| `.github/workflows/tests.yml` | the test lanes (offline first, then browser, checks, visual, platform, matrix, render), evidence publishing and notification |
| `.github/workflows/secret-scan.yml` | gitleaks over the full history on every push of every branch and every pull request |
| `.github/workflows/mutation.yml` | the mutation score gate, nightly and on dispatch |
| `.github/workflows/release.yml` | the release verdict on a tag or on dispatch |

Every rule the workflows follow (concurrency, time budget, retention, pinned container,
nightly switch, manual-run inputs, per-environment credentials, external config binding,
token-only lane, evidence publishing, notification) is documented in
[docs/features/ci-workflow.md](docs/features/ci-workflow.md) and checked offline by
`python -m gate.workflow_lint`.

---

## 15. Code Quality

```bash
ruff check .
ruff format .
```

Enforced rules: E, F, I (isort), UP (pyupgrade), B (bugbear). Line length: 100. Target: Python 3.10+.

Pre-commit hooks run ruff on every commit. All checks must pass before commit is accepted.

---

## 16. Configuration & Environments

Environments are declared in `config/environments.json` (`local`, `qa`, `staging`).

```bash
pytest --env qa        # default
pytest --env staging
TEST_ENV=staging pytest
```

Precedence, highest first (`core.config_reader`):

1. **CLI flag** — `--env` picks the environment (then `TEST_ENV`, then `qa`);
2. **environment variable** — every key under its upper-case spelling (`base_url` ->
   `BASE_URL`, `auth_mode` -> `AUTH_MODE`);
3. **`.env` file** — the git-ignored `.env.<env>` in the repository root (template: `config/env/.env.example`);
4. **per-environment profile** — the environment's block in `config/environments.json`;
5. **defaults** — the caller's default.

A blank value counts as absent at every rung. An unknown environment fails at start and lists
the declared ones. Per-environment login variables (`credentials` block) are described in
[docs/features/ci-workflow.md](docs/features/ci-workflow.md#per-environment-credentials).

---

## Further documentation

| Document | What it answers |
|---|---|
| [docs/features/](docs/features/README.md) | one page per area: what it does, how to use it, configuration, example |
| [docs/whats-live.md](docs/whats-live.md) | what runs in the default gate, in an opt-in lane, locally only, or is experimental |
| [docs/ci-decisions.md](docs/ci-decisions.md) | why each CI rule exists, what it prevents, how to change it safely |
| [docs/green-is-evidence.md](docs/green-is-evidence.md) | when a green run counts as evidence, and how to read one |
| [docs/artefacts.md](docs/artefacts.md) | every machine-readable artefact a run writes, with committed samples |

---

## AI Test Plugin Framework

The framework ships with a multi-tier plugin system that extends the base test automation with AI-driven analysis, generation, and governance capabilities.

### Architecture

```
plugins/
  _base_plugin.py      — BasePlugin ABC (execute/dry_run/run), PluginResult, PluginPriority
  cost_governor.py     — LLM budget tracking, model downgrade, prompt-level caching
  registry.py          — Auto-discovers *.plugin.py files, dispatches by trigger event
  tier1/               — Foundational plugins (unit-ai, integration-graph, api-contract, e2e-playwright)
  tier2/               — Quality & Safety (visual-regression, security-scan, accessibility-a11y, mutation-testing)
  tier3/               — Performance & Resilience (load-performance, fault-injection, property-fuzz)
  tier4/               — Novel (behavioral-equivalence, test-meta-quality, llm-output-oracle,
                         business-rule-compliance, state-machine-exhaustive,
                         synthetic-data-edge, temporal-regression)

orchestration/
  storage.py           — SQLite persistence (7 tables: runs, results, findings, costs, edge cases, snapshots, compliance)
  master_orchestrator.py — Runs plugins by priority tier, computes health score, fires deploy webhook
  evolution_agent.py   — Reads run history, uses Claude to generate improvement proposals
```

### Running the orchestrator

```bash
# Run all plugins for a trigger event
python -m orchestration.master_orchestrator --trigger manual

# Dry-run — no side effects, just report what would happen
python -m orchestration.master_orchestrator --trigger deploy --dry-run

# Run evolution agent to generate improvement proposals
python -m orchestration.evolution_agent
```

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLUGIN_BUDGET_USD` | `5.0` | Total LLM spend cap per orchestrator run |
| `ANTHROPIC_API_KEY` | — | Claude API key (plugins degrade gracefully without it) |
| `DEPLOY_WEBHOOK_URL` | — | URL to POST health score + deploy decision |
| `COMPLIANCE_WEBHOOK_URL` | — | URL to POST compliance check failures |
| `PROD_EVENT_LOG_PATH` | — | Path to JSONL event log for temporal regression plugin |

### Health score formula

Plugins are weighted by priority tier:

- **CRITICAL** (40%) — sequential, stops on first failure
- **HIGH** (35%) — parallel execution
- **NORMAL** (25%) — parallel execution
- **BACKGROUND** (0%) — fire-and-forget daemon threads

`health_score >= 70` → `deploy=True`

### Writing a new plugin

1. Create `plugins/tier{N}/my_plugin.plugin.py`
2. Inherit from `plugins._base_plugin.BasePlugin`
3. Set `name`, `priority`, `trigger_conditions`
4. Implement `run(context: dict) -> PluginResult`
5. Respect `context.get("dry_run")` — skip side effects when True
6. Add `if __name__ == "__main__":` block with `--dry-run` arg support

The registry auto-discovers the plugin on next scan. No registration needed.

### Plugin outputs

Generated artifacts are written to:

```
ai_generated_tests/
  unit/         — AI-generated unit tests (unit-ai plugin)
  integration/  — Integration test stubs (integration-graph plugin)
  a11y/         — Axe-core accessibility scripts (accessibility-a11y plugin)
  load/         — k6 load test scripts (load-performance plugin)
  property/     — Hypothesis property tests (property-fuzz plugin)
  state_machine/ — State transition tests (state-machine-exhaustive plugin)
  synthetic/    — Extreme profile JSON + Playwright tests (synthetic-data-edge plugin)

reports/
  plugin_runs.db           — SQLite database of all runs
  visual_baselines/        — Screenshot baselines (visual-regression plugin)
  behavioral_snapshots/    — Function output snapshots (behavioral-equivalence plugin)
  test_quality/            — Per-file quality scores (test-meta-quality plugin)
  evolution/               — LLM-generated improvement proposals
```
