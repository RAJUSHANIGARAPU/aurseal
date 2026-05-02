# Vision — wiki_project

## What This Is

A **universal, plug-and-play test automation framework** that works for any company, any project, any stack — with zero org-specific assumptions.

Python, Pytest, Playwright. Self-healing. AI-powered. Built to be adopted in minutes, not weeks.

## The One Rule

**Zero org assumptions.** No proprietary UI frameworks, no domain-specific data models, no internal tooling dependencies.

Every capability must be expressible in terms any engineering team on the planet can understand and apply immediately.

## End Goal — Fully Autonomous, Humanless Framework

The target state requires no human intervention in the test lifecycle:

| Concern | Human today | Target (AI) |
|---------|-------------|-------------|
| Test authoring | Engineer writes tests | Config/schema is the only input; tests generated automatically |
| Locator maintenance | Engineer fixes broken selectors | AI detects failure, heals selector, persists fix, retries |
| Failure triage | Engineer reads logs and traces | AI diagnoses root cause, proposes fix, applies it, reruns |
| CI management | Engineer monitors pipelines | AI manages full cycle end-to-end |
| Regression analysis | Engineer compares runs | AI detects, bisects, reports with fix proposals |

## Build Principles

- **Plug-and-play** — clone, configure one URL, run. No framework-specific knowledge required
- **Progressive** — every addition builds on what exists; existing tests never break
- **Universal** — patterns are framework-agnostic; no assumption about the UI stack being tested
- **AI-first** — self-healing, AI triage, and autonomous fix loops are first-class citizens, not add-ons
- **Transparent** — every AI decision is logged; humans can audit, override, or learn from it

## How This Grows

New capabilities are proven in a real-world project context first, then contributed here in Python — generalised and stripped of any org-specific logic. The framework grows from real usage, not hypothetical design.

## Current Capability Map

| Capability | Status |
|-----------|--------|
| Base page + locator strategy | Working |
| Config reader (env-aware) | Working |
| Playwright factory (ThreadLocal equiv) | Working |
| AI self-healing (Claude API) | Working (`core/ai/`) |
| Auto-fix loop | Working (`scripts/auto_runner.py`) |
| Trace analysis skill | Working |
| Test generation from trace | Working |
| API client layer | Working (`api/clients/`) |
| UI page objects | Working (`ui/pages/`) |
| Dynamic test generation from schema | Roadmap |
| State machine traversal | Roadmap |
| Full humanless pipeline | Roadmap |
