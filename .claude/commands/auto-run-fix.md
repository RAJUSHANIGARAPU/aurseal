# auto-run-fix

Autonomously run tests, analyze failures, fix code, rerun — until all pass.

## Usage

```
/auto-run-fix [-k "test_name_filter"] [--max-iterations N]
```

## Steps

Run the autonomous loop script:

```bash
cd /Users/RajuS/Documents/rajan_werk_github/wiki_project
python scripts/auto_runner.py
```

With a filter:
```bash
python scripts/auto_runner.py -k "test_wiki_search" --max-iterations 3
```

Analyze only (no code changes):
```bash
python scripts/auto_runner.py --no-fix
```

## What it does

1. Runs pytest with `--junit-xml`
2. If failures: calls `LogAnalyzer` + `TraceAnalyzer` via Claude API
3. Calls `AutoFixer` to apply minimal targeted fixes to the failing files
4. Reruns tests
5. Repeats up to N iterations (default: 5)
6. Reports BUILD SUCCESS or final failure with diagnosis

## Decision tree for common failures

- `TimeoutError: waiting for selector` → wrong locator; use data-testid, aria-label, or role
- `expect(locator).to_be_visible()` timeout → add `page.wait_for_load_state("networkidle")` before
- `AssertionError` on text → page not fully loaded; add `expect(heading).to_be_visible()` first
- `ElementHandle is detached` → page re-rendered; re-resolve the locator

Read `docs/ai_learnings.md` for full pattern reference.
