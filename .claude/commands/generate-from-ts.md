---
description: "Convert a playwright codegen TypeScript recording into a page object, a pytest module, a locator file and a test-data file, then fix it until it passes three times in a row. Manual only."
disable-model-invocation: true
---

# generate-from-ts

## Usage

```
/generate-from-ts <path/to/recording.spec.ts> [flow_name]
```

`flow_name` is snake_case; without it the name comes from the first navigated URL.

## Steps

1. **Clean the recording** and see what exists already:

   ```bash
   python -m tools.slash.recording clean "$RECORDING" --name "$FLOW"
   ```

   The helper drops sign-in and consent steps (fixtures authenticate), collapses
   cursor-key and retyping noise into each field's final value, drops focus clicks and
   turns overlay/backdrop clicks into an explicit wait, keeps only the last of repeated
   clicks, and groups the rest by intent: navigate, fill, submit, verify. It lists
   selectors built on auto-generated ids: replace each with a semantic one (look the
   element up in the app or a trace). When a page object, test or locator file for the
   flow already exists, **extend it; never overwrite or duplicate it**.

2. **Read the style references** before writing code: `docs/ai_learnings.md` (known
   patterns), `core/base_page.py`, one page object in `ui/pages/` and one test in
   `ui/tests/`.

3. **Locator and data files.** `--write` writes `ui/locators/<flow>_locators.json` in the
   registry format and `ui/testdata/<flow>.json` with every literal from the recording,
   merging into existing files (existing keys win):

   ```bash
   python -m tools.slash.recording clean "$RECORDING" --name "$FLOW" --write
   ```

   Selector order: test id, role + accessible name, placeholder, visible text, and CSS only
   when nothing semantic exists - never a styling class name.

4. **Page object** `ui/pages/<flow>_page.py`: extends `BasePage`, loads its own locator
   file, resolves every element with `self.resolve("<key>")`, methods named by intent,
   waits through Playwright expectations or load-state waits. For date-like inputs that
   refuse a bulk fill, type key by key.

5. **Test module** `ui/tests/test_<flow>.py`: the shared fixtures, its data loaded from the
   data file, only page-object calls, one test per scenario, `smoke` / `regression`
   markers, assertions with messages.

6. **Compile and lint** - no sleeps, no raw selectors outside the locator file, no direct
   Playwright calls in the test, no recorded literal in code:

   ```bash
   python -m py_compile ui/pages/${FLOW}_page.py ui/tests/test_${FLOW}.py
   python -m tools.slash.recording lint --page ui/pages/${FLOW}_page.py \
     --test ui/tests/test_${FLOW}.py --data ui/testdata/${FLOW}.json
   ```

7. **Reachability**: stop when the app does not answer 200 or 302:
   `python -m tools.slash.reach --accept 200,302`.

8. **Run and fix** until 3 consecutive passes, at most 5 fix rounds (any code change
   resets the green count):

   ```bash
   python -m tools.slash.stability --state reports/ts-loop.json reset
   pytest ui/tests/test_${FLOW}.py --junitxml=reports/junit-ts.xml
   python -m tools.slash.stability --state reports/ts-loop.json record --junit reports/junit-ts.xml
   ```

   On red: failed cases (`python -m tools.slash.junit --file reports/junit-ts.xml`), the
   newest trace, the log tail and `docs/ai_learnings.md`, then fix by symptom:

   | Symptom | Fix |
   |---|---|
   | timeout | wait for the element or state the step needs |
   | strict mode: several matches | a more specific semantic locator |
   | value lost after input | fill, read back, re-enter |
   | custom dropdown does not open on click | open it the way the component expects, then pick by role |
   | wrong text, page not settled | wait for the settled state before reading |
   | unexpected login redirect | session fixture, not a recorded sign-in |
   | test id not found | check the attribute in the app; fall back down the selector order |

9. **Commit** the four files only after 3 consecutive passes, and give the push command.

## Stop conditions

The app is unreachable, or 5 fix rounds pass without 3 consecutive passes: report each
attempt, the remaining traceback and `playwright show-trace <newest trace>`, and commit
nothing.
