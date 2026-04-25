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
8. [AI Skills (Slash Commands)](#8-ai-skills-slash-commands)
9. [Failure Evidence Bundles](#9-failure-evidence-bundles)
10. [Markers & Segmentation](#10-markers--segmentation)
11. [Docker Execution](#11-docker-execution)
12. [CI Integration](#12-ci-integration)
13. [Code Quality](#13-code-quality)
14. [Configuration & Environments](#14-configuration--environments)

---

## 1. Overview

This framework covers the full automation lifecycle end-to-end:

- **UI tests** via Playwright — page objects, locators, flows, traces, video
- **API tests** via a six-agent autonomous pipeline — ingest, generate, execute, analyse, heal, repeat
- **AI self-healing** — broken locators are detected, repaired by Claude, and persisted without human intervention
- **Failure bundles** — every test failure writes a structured JSON evidence file with screenshot, stack trace, console errors, and failed HTTP requests
- **Autonomous fix loops** — run → analyse → patch → rerun until green

---

## 2. Architecture

### UI Layer

```
Tests → Flows → Pages → BasePage → Locators (JSON) → Playwright
                                 → AI Self-Healing (core/ai/)
```

### API Agent Pipeline

```
Orchestrator
  ├── IngestionAgent      — reads Postman collections / OpenAPI specs
  ├── TestGenerationAgent — produces parameterised test cases
  ├── ExecutionAgent      — runs requests, captures responses
  ├── AnalysisAgent       — diagnoses failures, proposes fixes
  └── SelfHealingAgent    — patches broken assertions, reruns
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
│   └── api_agent/       — unit tests for all agent and engine components
├── config/
│   └── environments.json
├── generated_tests/     — output directory for agent-generated test files
├── reports/             — screenshots, videos, traces, logs, failure bundles
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

## 8. AI Skills (Slash Commands)

Use these from the repo root inside Claude Code:

| Command | What it does |
|---------|-------------|
| `/analyze-trace` | Extracts actions, errors, and DOM state from a Playwright trace ZIP |
| `/analyze-test-failure` | Reads failure bundles and diagnoses root cause with fix proposals |
| `/auto-run-fix` | Autonomous loop: run → analyse → fix → rerun until green |
| `/generate-test-from-trace` | Generates Page Object + test from a recorded trace |

Skills are in `.claude/commands/`.

---

## 9. Failure Evidence Bundles

Every failing test writes a JSON bundle to `reports/failures/<test>-<timestamp>.json`:

```json
{
  "test": "test_login_flow",
  "timestamp": "2026-04-25T09:31:00Z",
  "error": "AssertionError: expected 'Dashboard' in page title",
  "stackTrace": "...",
  "screenshot": "<base64 PNG>",
  "consoleErrors": ["TypeError: Cannot read properties of null"],
  "failedRequests": ["POST /api/auth/login → 401"]
}
```

The bundle is written by `core/failure_reporter.py` via the `pytest_runtest_makereport` hook in `conftest.py`. No test code changes required.

---

## 10. Markers & Segmentation

```bash
pytest -m smoke
pytest -m regression
pytest -m e2e
pytest -m negative
pytest -m api
pytest -m contract
```

---

## 11. Docker Execution

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

## 12. CI Integration

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

## 13. Code Quality

```bash
ruff check .
ruff format .
```

Enforced rules: E, F, I (isort), UP (pyupgrade), B (bugbear). Line length: 100. Target: Python 3.10+.

Pre-commit hooks run ruff on every commit. All checks must pass before commit is accepted.

---

## 14. Configuration & Environments

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
