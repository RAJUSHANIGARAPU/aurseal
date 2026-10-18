---
description: "Run a failing UI suite (or a -k selection) until it is green three times in a row, diagnosing each red run and fixing selectors, waits and synchronisation in page objects and helpers only. Manual only."
disable-model-invocation: true
---

# auto-run-fix

Bring a red pytest + Playwright selection to a stable green without changing what any test
expects. Start it yourself; never start it on your own initiative.

## Usage

```
/auto-run-fix [-k "<filter>"] [env]
```

Both arguments are optional. `env` defaults to the repository default (`qa`).
When `scripts/auto_runner.py` covers what is asked, prefer it; this procedure is the manual
equivalent with the stopping rules spelled out.

## The loop

The loop state lives in `reports/auto-run-fix.json` and `tools.slash.stability` owns the
rules: **3 consecutive green runs** to finish, **at most 5 fix rounds**, and an early stop
when the **same error survives 3 rounds**. A run that executed no tests is not green.

1. Reset the counters:

   ```bash
   python -m tools.slash.stability --state reports/auto-run-fix.json reset
   ```

2. Run the selection with JUnit output and show only the tail of the console:

   ```bash
   pytest ui/tests -k "$FILTER" --env "$ENV" \
     --junitxml=reports/junit-auto-fix.xml 2>&1 | tail -40
   python -m tools.slash.stability --state reports/auto-run-fix.json record \
     --junit reports/junit-auto-fix.xml
   ```

3. The second command prints the next step:
   - `rerun` - green but not yet three in a row. Say "green N of 3, re-running for
     stability" and go back to step 2.
   - `done` - three consecutive greens. Go to the report.
   - `fix` - red. The green count is back to 0 and one fix round is used. Diagnose (step 4).
   - `give-up` - five rounds used, or one error unchanged for three rounds. Go to the
     failure report. Do not commit anything.

4. Diagnose a red run:
   - list every failed and errored case with the start of its message:
     `python -m tools.slash.junit --file reports/junit-auto-fix.xml`;
   - find the newest trace: `ls -t reports/traces/*.zip | head -1`;
   - read `docs/ai_learnings.md` (the known failure patterns) before changing anything;
   - follow the stack trace to the exact page-object method and read it;
   - classify and apply the smallest fix of that kind:

     | Failure | Fix |
     |---|---|
     | wrong or fragile selector | a semantic one: test id, ARIA label, role (in the locator JSON) |
     | element not ready | wait for an explicit ready condition (`expect(...).to_be_visible()`, `to_be_enabled()`) |
     | page not settled | wait for the load state or for the element the next step needs |
     | stale element after a re-render | resolve the element again right before using it |
     | value lost after input | fill, then read back and re-enter when it did not land |

5. Before committing a fix, prove it touches no assertion, expectation or test data:

   ```bash
   git diff HEAD | python -m tools.slash.diff_guard
   ```

   Then commit just that fix, with a message saying what broke and why, and go back to step 2.
   Each fix is its own commit.

## Rules

- Never change assertions, expected values, test data or business logic. Only selectors,
  waits and synchronisation in page objects (`ui/pages/`, `ui/locators/`) and helpers
  (`ui/components/`, `core/`). The diff guard above enforces it.
- No fixed sleeps: no `time.sleep`, no `wait_for_timeout`.
- Nothing is committed while the run is red, and nothing broken is committed when the loop
  gives up.

## Report

- **Done:** the filter used, the three green runs, the number of fix rounds (0 means green
  on the first try), every file changed (`git diff --stat <start>..HEAD`), and the push
  command for the current branch: `git push -u origin "$(git branch --show-current)"`.
- **Gave up:** what was tried in each round, the remaining error with its full traceback, and
  the suggestion to open the newest trace: `playwright show-trace <newest trace>`.
