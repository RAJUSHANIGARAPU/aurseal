# generate-from-prd

Read a Product Requirements Document (PRD, user story, acceptance criteria, or any requirements
text) and generate a full test suite from it — spec → Page Object + pytest → run → push-ready.

## Usage

```
/generate-from-prd <path/to/prd.md | url | text-blob> [flowName]
```

Examples:
- `/generate-from-prd docs/prd-search.md article_search`
- `/generate-from-prd https://github.com/org/wiki/issues/42`
- `/generate-from-prd "Users can search for articles by keyword and filter by date"`

---

## Phase 1 — Ingest the PRD

**File:**
```bash
cat "$PRD_FILE"
```

**URL:**
```bash
curl -s "$PRD_URL" | python3 -c "
import sys, re
html = sys.stdin.read()
text = re.sub(r'<[^>]+>', ' ', html)
print(re.sub(r'\s+', ' ', text)[:8000])
"
```

**Inline text:** use directly.

---

## Phase 2 — Extract testable scenarios

Analyze the PRD and produce:

1. **Happy path** — the golden path
2. **Validation errors** — missing/invalid inputs
3. **Edge cases** — boundaries mentioned
4. **Negative** — what should be blocked/prevented

Format each as:
```
Scenario: <name>
  Given: <precondition>
  When:  <user action>
  Then:  <expected outcome>
  Tags:  <ui|api|smoke|regression>
```

---

## Phase 3 — Write spec files

```
specs/<flowName>-<feature>.md
```

Use the format already defined in `specs/`. Commit spec files immediately — they are permanent.

---

## Phase 4 — Classify and generate

| Scenario type | Output |
|---------------|--------|
| UI interaction (Playwright) | `ui/pages/<flow>_page.py` + `ui/tests/<flow>_test.py` |
| API call | `api/tests/test_<flow>_api.py` + client class |
| Mixed | Both |

Follow:
- `/generate-from-ts` rules for UI code (BasePage, resolve(), no time.sleep())
- `/generate-from-swagger` rules for API code

---

## Phase 5 — Syntax check

```bash
python3 -m py_compile ui/pages/*.py ui/tests/*.py api/tests/*.py 2>&1
```

---

## Phase 6 — Run → auto-fix loop (3 passes, max 5 iterations)

```bash
source venv/bin/activate 2>/dev/null || true
pytest --env=development -k "<test_function_name>" -q --tb=short 2>&1 | tail -60
```

Use the same fix decision trees as `/generate-from-ts` (UI) and `/generate-from-swagger` (API).

---

## Phase 7 — Commit and report

```bash
git add specs/ ui/pages/ ui/tests/ api/tests/ ui/locators/ ui/testdata/
git commit -m "feat(e2e): <flowName> — generated from PRD, passed 3×"
```

---

## Notes

- Always commit spec files — they document WHAT was tested and WHY
- If the PRD mentions specific test users or data, add a `# TODO: seed test data` comment
- Vague requirements ("user can manage content") → ask for clarification before generating
- PRD-driven tests verify business requirements, not implementation — most valuable tests
