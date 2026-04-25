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
| `/auto-run-fix` | Autonomous loop: run → analyse → fix → rerun until green |
| `/generate-test-from-trace` | Generates Page Object + test from a recorded trace |

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

---

## 13. Docker Execution

### UI Tests

```bash
docker compose build
docker compose run tests

# Override browser
docker compose run tests --browser firefox

# Parallel
docker compose run tests -n 4
```

Artifacts are written to the local `reports/` directory via volume mount.

### API Agent

```bash
docker compose run api-agent run --collection api/postman/sample_collection.json
```

---

## 14. CI Integration

```
.github/workflows/tests.yml
```

Pipeline steps:
- Ruff lint check
- Playwright browser install
- UI test execution with artifact upload
- Agent unit test execution
- Failure bundle and trace archival

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

```
config/environments.json
```

Switch environment:

```bash
pytest --env qa        # default
pytest --env staging
pytest --env prod
```

The `ConfigReader` class resolves base URLs per environment. Add new environments by extending `environments.json` — no code changes needed.
