---
description: "Use to run the suite (or a selection) and get a readable health report with a release-gate verdict: pass/fail counts, failing tests, healing events since the run started, flakiness signals, and Block / Warn / Pass."
---

# test-health-report

## Usage

```
/test-health-report [pytest args, e.g. -m smoke or -k search]
```

May be started by the user or by the assistant.

## Steps

1. Count the test modules available (`ls ui/tests/test_*.py api/tests/test_*.py | wc -l`).

2. Run everything through the helper:

   ```bash
   python -m tools.slash.health --env "${ENV:-qa}" -- $PYTEST_ARGS
   ```

   It checks that the base URL answers within 10 s and **stops before running anything**
   when the application is down; records the start time (UTC); runs pytest with JUnit
   output, keeping the full console log in a temporary file and showing its tail; then
   writes the report.

3. The report (saved as `reports/health/test-health-<timestamp>.md` and printed in full):
   - header: date and time, environment, branch, duration;
   - summary table: passed, failed (failures + errors), skipped, healed - or
     "not measured";
   - failed tests with class, name and the start of the message (an unreadable XML is
     listed, not fatal);
   - healing events from `reports/ui_healing_sessions.jsonl` at or after the run start
     (applied or not, test, failure type, strategy, files patched). A missing or empty log,
     or no entries in the window, is **not measured** with the reason - never zero. An
     ordinary pytest run does not heal; only the healing orchestrator writes this log;
   - flakiness: retried tests from the console log and the slowest tests;
   - the gate.

## Gate rules

- **Block**: any failure or error.
- **Warn**: no failures, but one or more heals were applied (locators changed; review
  before release).
- **Pass**: no failures or errors and no applied heal. With healing not measured the gate
  is still Pass, and the report states that healing was not measured.

Print the whole report back to the user, with the saved path.
