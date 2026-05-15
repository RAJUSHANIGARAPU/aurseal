# generate-from-ts

Read a Playwright TypeScript recording (.spec.ts from `playwright codegen`) and produce a
complete, tested, push-ready Python test — Page Object + pytest file + locators JSON + testdata —
then run it 2-3 times locally and auto-fix until stable.

## Usage

```
/generate-from-ts <path/to/recording.spec.ts> [flowName]
```

- `path`: absolute or relative path to the `.ts` file
- `flowName`: optional short name (e.g. `article_search`). Inferred from page URL if omitted.

---

## Phase 1 — Read and understand the recording

### 1a. Read the .ts file

```bash
cat "$TS_FILE"
```

### 1b. Infer the flow name

Look for `page.goto('...')` to extract the base domain/path and derive a snake_case name.
Use the `flowName` argument if provided.

### 1c. Check for existing files

```bash
ls ui/pages/${flowName}_page.py 2>/dev/null && echo "EXISTS" || echo "MISSING"
ls ui/tests/${flowName}_test.py 2>/dev/null
ls ui/locators/${flowName}_locators.json 2>/dev/null
```

Read existing files if they exist — the recording may extend a page object that already covers part of the flow.

---

## Phase 2 — Clean the recording

Strip the following noise before conversion:

| Strip | Why |
|-------|-----|
| `page.goto('...auth0...login...')` and ALL login/cookie steps until the actual app URL | Framework handles auth via fixtures/conftest — no browser login needed in tests |
| Sequences of `press('ArrowLeft')` / `press('ArrowRight')` on a textbox | Typo-correction artefacts; keep only the final `.fill(value)` |
| `page.locator('.cdk-overlay-backdrop').click()` | Accidentally closed overlay — omit |
| Any `#mat-select-value-N` or `#mat-mdc-form-field-label-N` selector | Generated IDs — replace with semantic selector |
| Repeated clicks on the same element trying to open a menu | Keep only the final successful interaction |
| `page.locator('mat-sidenav-content').click()` | Defocus click — replace with `page.wait_for_load_state("networkidle")` |

After stripping, group remaining steps into logical phases by intent (navigation, form fill, submit, assertion).

---

## Phase 3 — Generate Python code

**Always read these before writing any Python:**
- `docs/ai_learnings.md` — Playwright Python patterns and timing rules
- `core/base_page.py` — BasePage API (`self.resolve(key)`, locator types)
- `ui/pages/home_page.py` — style reference for page objects
- `ui/tests/` — style reference for pytest tests

### 3a. Locators JSON

File: `ui/locators/${flowName}_locators.json`

Use this structure (same format as `ui/locators/wiki_locators.json`):

```json
{
  "element_key": {
    "type": "testid|role|css|text|placeholder",
    "value": "...",
    "role": "button",
    "name": "Submit"
  }
}
```

Selector priority (never break this order):
1. `"type": "testid"` — `data-testid` attribute (strongest)
2. `"type": "role"` — ARIA role + accessible name
3. `"type": "placeholder"` — input placeholder text
4. `"type": "text"` — visible text (last resort)
5. `"type": "css"` — CSS selector (only when no semantic option exists; never raw class names)

### 3b. Testdata JSON

File: `ui/testdata/${flowName}_data.json`

Extract all test data values (search keywords, form inputs, expected results).
Never hardcode values in page objects or test files.

### 3c. Page Object

File: `ui/pages/${flowName}_page.py`

Rules:
- Extends `BasePage` from `core.base_page`
- Constructor: `def __init__(self, page, config)` — calls `super().__init__(page, config)`
- Loads its own locator file (override `self.locators` from a flow-specific JSON)
- All element interactions go via `self.resolve("key")` — never raw selectors in method bodies
- Methods are named for intent (`navigate`, `search`, `submit_form`, `assert_result_visible`)
- Waits: use `expect(locator).to_be_visible(timeout=N)` or `page.wait_for_load_state("networkidle")`
- Never `time.sleep()` — use Playwright built-in waits only

**Mapping table — TS codegen → Python:**

| TypeScript codegen | Python page object |
|--------------------|--------------------|
| `getByRole('button', {name: 'X'}).click()` | `self.page.get_by_role("button", name="X").click()` or `self.resolve("key").click()` |
| `getByRole('textbox').fill('val')` | `self.resolve("input_key").fill(val)` |
| `getByTestId('x').click()` | `self.page.get_by_test_id("x").click()` |
| Date input `.fill()` | `locator.press_sequentially(value, delay=80)` — never `fill()` on Angular/dynamic date inputs |
| `#mat-select-value-N` + option click | `locator.dispatch_event("click")` then option selection |
| `locator.waitFor()` | `expect(locator).to_be_visible(timeout=N)` |
| `page.waitForURL('...')` | `page.wait_for_url("**pattern**")` |
| `page.waitForLoadState(...)` | `page.wait_for_load_state("networkidle")` |

### 3d. Pytest test file

File: `ui/tests/${flowName}_test.py`

Rules:
- Uses `page` and `config` fixtures from `conftest.py`
- Loads testdata from `ui/testdata/${flowName}_data.json`
- No direct Playwright calls — ALL interactions go through the page object
- One test function per logical scenario in the recording
- Markers: `@pytest.mark.smoke` or `@pytest.mark.regression` as appropriate
- Assertions use `assert` with a descriptive message, or `expect()` from `playwright.sync_api`

---

## Phase 4 — Compile / syntax check

```bash
cd /Users/RajuS/Documents/rajan_werk_github/wiki_project
python -m py_compile ui/pages/${flowName}_page.py && echo "OK"
python -m py_compile ui/tests/${flowName}_test.py && echo "OK"
```

Fix any syntax or import errors before proceeding.

---

## Phase 5 — Run → auto-fix loop

### Pre-flight: check environment

```bash
BASE_URL=$(grep base_url config/development.yml 2>/dev/null | awk '{print $2}' | tr -d '"')
curl -s -o /dev/null -w "%{http_code}" "$BASE_URL" --max-time 10
```

If response is not 200/302: **stop and tell the user the target app is not reachable.**

### Run loop — up to 5 fix iterations, targeting 3 consecutive passes

```bash
cd /Users/RajuS/Documents/rajan_werk_github/wiki_project
source venv/bin/activate 2>/dev/null || true
pytest --env=development -k "${TEST_FUNCTION_NAME}" --tb=short -q 2>&1 | tail -80
```

**On PASSED:**
- Increment pass counter
- If pass counter == 3 → exit loop, go to Phase 6
- Else run again

**On FAILED — collect evidence:**

```bash
# JUnit XML report
find reports -name "*.xml" | xargs grep -l "failure\|error" 2>/dev/null | head -3
cat reports/junit/*.xml 2>/dev/null | grep -A 20 "<failure\|<error"

# Most recent trace
ls -t reports/traces/*.zip 2>/dev/null | head -1

# stdout from last run
cat reports/last_run.log 2>/dev/null | tail -50
```

**Always read `docs/ai_learnings.md` before fixing.**

**Fix decision tree:**

| Symptom | Fix |
|---------|-----|
| `TimeoutError` on locator | Add `page.wait_for_load_state("networkidle")` before the interaction |
| `strict mode violation` (multiple matches) | Add `.first` to the locator |
| `fill()` on Angular date input loses value | Switch to `press_sequentially(value, delay=80)` |
| `dispatch_event("click")` needed on mat-select | Replace `.click()` with `.dispatch_event("click")` |
| Dropdown value disappears after selection | Add `page.wait_for_load_state("networkidle")` + `page.wait_for_timeout(1000)` after selection |
| `AssertionError` — element text wrong | Check if a `wait_for_load_state` is missing before the assertion |
| `page.goto()` auth redirect | Ensure conftest auth fixture ran; check `BASE_URL` in config |
| Locator not found by testid | Try role or text fallback; update locators JSON |

**After applying a fix:**
- Re-run `python -m py_compile` check
- Re-run the test
- Reset pass counter to 0 after any code change

**If still failing after 5 iterations:**
- Report what was tried, remaining error, full traceback
- Tell the user to open the trace at https://trace.playwright.dev
- Do NOT commit or push

---

## Phase 6 — Report push-readiness

When 3 consecutive passes are achieved:

```bash
cd /Users/RajuS/Documents/rajan_werk_github/wiki_project
git add \
  ui/pages/${flowName}_page.py \
  ui/tests/${flowName}_test.py \
  ui/locators/${flowName}_locators.json \
  ui/testdata/${flowName}_data.json
git commit -m "feat(e2e): ${flowName} test — generated from TS recording, passed 3× locally"
```

Report:
```
✓ Generated:
  - ui/pages/${flowName}_page.py
  - ui/tests/${flowName}_test.py
  - ui/locators/${flowName}_locators.json
  - ui/testdata/${flowName}_data.json

✓ Passed 3× locally on branch: <branch-name>

Push when ready:
  git push -u origin <branch-name>
```

---

## Important constraints

- No direct Playwright calls in test files — only page object methods
- No hardcoded selectors in page or test files — everything through `BasePage.resolve()` or named locator JSON keys
- No `time.sleep()` — use `wait_for_load_state` or `expect().to_be_visible(timeout=N)`
- Auth/login steps from the recording are always stripped — conftest handles auth
- Never push a test that hasn't passed 3× locally
