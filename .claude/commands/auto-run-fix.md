# auto-run-fix

Autonomously run tests, analyze failures, fix code, and rerun until the suite passes
**3 consecutive times** — no human intervention needed.

## Usage

```
/auto-run-fix [-k "test_name_filter"] [--env development]
```

Examples:
- `/auto-run-fix` — run full suite
- `/auto-run-fix -k "test_article_search"`
- `/auto-run-fix -k "test_wiki_login" --env development`

## Loop

Track two counters: `fix_attempts` (max 5) and `consecutive_passes` (target 3).

Repeat until `consecutive_passes == 3` or `fix_attempts == 5`:

### Step 1 — Run tests

```bash
cd /path/to/wiki_project
source venv/bin/activate 2>/dev/null || true
pytest <filter_args> --junit-xml=target/junit/results.xml -q --tb=short 2>&1 | tail -80
```

### Step 2 — Check result

If all tests pass (exit code 0):
  - Increment `consecutive_passes`
  - If `consecutive_passes == 3` → **DONE** — go to Phase: Report
  - If `consecutive_passes < 3` → log "Pass N/3 — running again to confirm stability" → go to Step 1

If any test failed (exit code != 0):
  - Reset `consecutive_passes = 0`
  - Increment `fix_attempts`
  - Continue to Step 3

### Step 3 — Collect failure evidence

```bash
# JUnit XML failures
python3 -c "
import glob, xml.etree.ElementTree as ET
for f in glob.glob('target/junit/*.xml'):
    root = ET.parse(f).getroot()
    for tc in root.iter('testcase'):
        for child in tc:
            if child.tag in ('failure', 'error'):
                print(f'{tc.get(\"classname\")}.{tc.get(\"name\")}')
                print(child.text[:800] if child.text else '(no message)')
                print()
"

# Most recent trace
ls -t target/traces/*.zip 2>/dev/null | head -1
```

### Step 4 — Diagnose

1. Read `docs/ai_learnings.md` for known patterns
2. Read the stack trace — identify the exact method and line that failed
3. Read that method in the Page Object file
4. Apply the fix decision tree:

| Symptom | Fix |
|---------|-----|
| `TimeoutError: waiting for selector` | Wrong locator — use `data-testid`, `aria-label`, or `role` |
| `expect(locator).to_be_visible()` timeout | Add `page.wait_for_load_state("networkidle")` before assertion |
| `AssertionError` on text content | Page not fully loaded — wait for a heading to be visible first |
| `ElementHandle is detached` | Page re-rendered — re-resolve locator inside the assertion |
| `StaleElementReferenceError` | Same as above — use `page.locator()` not stored element handles |
| Value disappears after input | Add `page.wait_for_load_state("networkidle")` after each form action |

### Step 5 — Fix

Apply the minimal targeted fix to the failing Page Object or test helper.
Never change test assertions or business logic — only fix locators and timing.

### Step 6 — Go to Step 1

---

## Phase: Report

When `consecutive_passes == 3`:

```
✓ PASSED 3× consecutively
  Test filter: <filter or "all">
  Passes:      run 1 ✓  run 2 ✓  run 3 ✓
  Fix rounds:  <N> (0 = passed first try)
  Changes:     <files modified, if any>

Push-ready: git push -u origin <branch>
```

---

## Max fix iterations: 5

If still failing after 5 fix attempts, report:
- What was tried in each round
- Remaining error with full stack trace
- Recommended next step (open trace ZIP in trace.playwright.dev)
- Do NOT commit broken code

## Important Notes

- Always read `docs/ai_learnings.md` before attempting a fix
- Never change test logic or assertions — only fix locators, timing, and selectors in Page Objects
- Commit each successful fix: `git commit -m "fix(test): <describe what broke and why>"`
- If the same error repeats across 3 fix rounds without progress → stop and report, don't loop on a dead end
- Use `python scripts/auto_runner.py` if it exists — it wraps the same logic with LogAnalyzer + AutoFixer
