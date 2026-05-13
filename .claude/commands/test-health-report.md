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
BASE_URL=$(python3 -c "import yaml; d=yaml.safe_load(open('config/development.yml')); print(d.get('base_url',''))" 2>/dev/null)
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
cd /Users/RajuS/Documents/rajan_werk_github/wiki_project
source venv/bin/activate 2>/dev/null || true
pytest --env=development ${ARGS} \
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

```bash
# Healing events from autonomous_ui
find reports -name "healing_*.json" -newer /tmp/wiki-health-run.log 2>/dev/null | head -10
for f in $(find reports -name "healing_*.json" -newer /tmp/wiki-health-run.log 2>/dev/null); do
  python3 -c "import json; d=json.load(open('$f')); print(d.get('test','?'), '|', d.get('locator_key','?'), '->', d.get('healed_selector','?'))" 2>/dev/null
done
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
**Environment:** development  |  **Branch:** <git branch>  |  **Duration:** <Xs>

### Summary
| Status    | Count |
|-----------|-------|
| ✓ Passed  | N |
| ✗ Failed  | N |
| ↷ Skipped | N |
| 🔧 Healed | N |

### Failed Tests
(class name, function name, first line of failure message)

### Healing Events
(test name, locator key, old selector → new selector)

### Flakiness Signals
(retried tests, unusually slow tests)

### Deploy Gate: PASS | WARN | BLOCK
```

**Deploy gate logic:**
- `PASS` — zero failures, zero errors
- `WARN` — zero test failures but healing events > 0 — locators were auto-fixed; review before deploying
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
- Healing events > 0 means the site changed; commit updated locator JSONs
- Fully autonomous framework goal: 0 failures, 0 healing events, 0 retries on a stable build
- Read `docs/ai_learnings.md` if you see repeated `TimeoutError` patterns
