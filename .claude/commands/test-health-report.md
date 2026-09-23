---
description: "Use before a merge or when asking how healthy the suite is: runs pytest, parses the JUnit XML, lists failures, healing events and flakiness signals, and gives a PASS/WARN/BLOCK deploy-gate call."
---

# test-health-report

Run the full test suite and produce a clean health report: pass/fail per test, healing events,
flakiness signals, and a deploy-gate recommendation.

## Usage

```
/test-health-report [pytest-args]
```

Examples:
- `/test-health-report` — run everything
- `/test-health-report -m smoke` — only smoke tests
- `/test-health-report -k test_search` — single test

---

## Phase 1 — Pre-flight

```bash
BASE_URL=$(python3 -c "import json; print(json.load(open('config/environments.json'))['qa']['base_url'])" 2>/dev/null)
curl -s -o /dev/null -w "%{http_code}" "$BASE_URL" --max-time 10
```

If not reachable: report that the target app is down and stop.

Count available tests:
```bash
find ui/tests -name "test_*.py" | xargs grep -l "^def test_\|^async def test_" | wc -l
```

---

## Phase 2 — Run tests

```bash
cd /path/to/wiki_project
source venv/bin/activate 2>/dev/null || true
RUN_START=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
pytest --env=qa ${ARGS} \
  --tb=short -q \
  --junit-xml=reports/health-run.xml \
  2>&1 | tee /tmp/wiki-health-run.log | tail -40
```

---

## Phase 3 — Parse results

```bash
# Summary from pytest stdout
grep -E "passed|failed|error|warning" /tmp/wiki-health-run.log | tail -5

# From JUnit XML
python3 - <<'EOF'
import xml.etree.ElementTree as ET, sys
try:
    tree = ET.parse("reports/health-run.xml")
    root = tree.getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    print(f"TOTAL:   {suite.get('tests', 0)}")
    print(f"PASSED:  {int(suite.get('tests',0)) - int(suite.get('failures',0)) - int(suite.get('errors',0)) - int(suite.get('skipped',0))}")
    print(f"FAILED:  {int(suite.get('failures',0)) + int(suite.get('errors',0))}")
    print(f"SKIPPED: {suite.get('skipped', 0)}")
    for tc in root.iter("testcase"):
        f = tc.find("failure") or tc.find("error")
        if f is not None:
            print(f"\n✗ {tc.get('classname')}.{tc.get('name')}")
            print(f"  {(f.text or f.get('message',''))[:200]}")
except Exception as e:
    print(f"Could not parse XML: {e}")
EOF
```

---

## Phase 4 — Check healing events

Healing decisions are appended to `reports/ui_healing_sessions.jsonl` by
`autonomous_ui/orchestrator.py` (`_log_session`), one JSON object per line with
`timestamp, test, failure_type, root_cause, confidence, strategy, applied, details, patched_files`.
Only `python -m autonomous_ui.orchestrator` writes it; a plain `pytest` run (Phase 2) never heals.
So an absent or empty log, or no rows since `RUN_START`, means **NOT MEASURED**, not zero heals.

```bash
python3 - "$RUN_START" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
log = Path("reports/ui_healing_sessions.jsonl")
lines = [l for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
if not lines:
    print("healing data: NOT MEASURED (reports/ui_healing_sessions.jsonl absent or empty)")
    sys.exit()
since = datetime.fromisoformat(sys.argv[1])
rows = [r for r in map(json.loads, lines) if datetime.fromisoformat(r["timestamp"]) >= since]
if not rows:
    print(f"healing data: NOT MEASURED (no orchestrator session since {sys.argv[1]})")
    sys.exit()
applied = [r for r in rows if r.get("applied")]
print(f"HEALED: {len(applied)} applied of {len(rows)} decisions")
for r in rows:
    files = ", ".join(r.get("patched_files") or []) or "-"
    print(f"{'applied' if r.get('applied') else 'not applied'} | {r.get('test','?')} | "
          f"{r.get('failure_type','?')} | {r.get('strategy','?')} | {files}")
PY
```

---

## Phase 5 — Flakiness signals

```bash
# Tests that were retried
grep -i "retry\|rerun\|attempt" /tmp/wiki-health-run.log | head -10

# Slowest tests
grep "PASSED\|FAILED" /tmp/wiki-health-run.log | grep -E "[0-9]+\.[0-9]+s" | \
  sort -t's' -k1 -rn | head -10
```

---

## Phase 6 — Build the report

Output a clean markdown report:

```
## Test Health Report — <date> <time>
**Environment:** qa  |  **Branch:** <git branch>  |  **Duration:** <Xs>

### Summary
| Status    | Count |
|-----------|-------|
| ✓ Passed  | N |
| ✗ Failed  | N |
| ↷ Skipped | N |
| 🔧 Healed | N, or NOT MEASURED |

### Failed Tests
(class name, function name, first line of failure message)

### Healing Events
(test, failure type, strategy, patched files — or `healing data: NOT MEASURED`)

### Flakiness Signals
(retried tests, unusually slow tests)

### Deploy Gate: PASS | WARN | BLOCK
```

**Deploy gate logic:**
- `PASS` — zero failures, zero errors
- `WARN` — zero test failures but applied heals > 0 — locators were patched; review before deploying.
  If healing data is NOT MEASURED, print that in the report; do not count it as 0 heals
- `BLOCK` — any test failures or errors — do not deploy

---

## Phase 7 — Save the report

```bash
REPORT_FILE="reports/health-report-$(date +%Y%m%d-%H%M%S).md"
echo "Report saved to: $REPORT_FILE"
```

Print the full report to the conversation.

---

## Notes

- Run before every PR merge to main
- Applied heals > 0 means the site changed; commit updated locator JSONs
- Fully autonomous framework goal: 0 failures, 0 healing events, 0 retries on a stable build
- Read `docs/ai_learnings.md` if you see repeated `TimeoutError` patterns
