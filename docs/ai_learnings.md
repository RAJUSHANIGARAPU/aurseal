# AI Learnings — wiki_project Playwright Automation

Accumulated patterns from live debugging sessions. Read this before writing or fixing tests.

---

## 1. Page Load Timing

**Problem**: Assertions fail because the page/element hasn't loaded yet.

**Solution**: Always wait for the page to be in a stable state before asserting:
```python
page.wait_for_load_state("networkidle")
# OR for a specific element
expect(page.locator("h1")).to_be_visible(timeout=10_000)
```

**Never**: `time.sleep(N)` — use load state or expect().

---

## 2. Locator Strategy (Stable Selectors)

Priority order (never use CSS classes or positional XPath):

1. `page.get_by_test_id("value")` → `data-testid` attribute
2. `page.get_by_role("button", name="Save")` → ARIA role
3. `page.get_by_label("Username")` → aria-label or label[for]
4. `page.get_by_placeholder("Search...")` → placeholder
5. `page.get_by_text("exact text")` → visible text (last resort)

**Never**: `page.locator(".css-class-name")` or `page.locator("//xpath")` with positional predicates.

---

## 3. Detached Element Handles

**Problem**: `ElementHandle is detached from DOM` — page re-rendered while holding a reference.

**Solution**: Re-resolve the locator after any navigation or dynamic update:
```python
# Wrong — holds handle across re-render
element = page.locator("h1").element_handle()

# Correct — re-resolve each time
expect(page.locator("h1")).to_contain_text("Expected", timeout=5_000)
```

---

## 4. Trace Timestamps

All traces are saved with timestamps in the filename: `{test_name}_{browser}_{YYYYMMDD_HHMMSS}.zip`.

View traces at: https://trace.playwright.dev (upload the ZIP).

---

## 5. Screenshot on Failure

Screenshots are automatically taken on test failure via `conftest.py` `pytest_runtest_makereport` hook.
Saved to: `reports/screenshots/{test_name}.png`.

---

## 6. Video Recording

Videos saved to: `reports/videos/{test_name}_{browser}_{timestamp}.webm`.

---

## 7. Network Requests in Tests

For API-level assertions, use `page.expect_response()`:
```python
with page.expect_response("**/api/search**") as resp_info:
    page.get_by_role("button", name="Search").click()
resp = resp_info.value
assert resp.status == 200
```

---

## 8. Auto-Fix Loop

The `scripts/auto_runner.py` loop:
1. Runs pytest
2. Calls Claude API to diagnose failures from JUnit XML + logs
3. Calls Claude API to generate targeted code fixes
4. Applies fixes and reruns

**Requires**: `ANTHROPIC_API_KEY` environment variable.

**Usage**:
```bash
python scripts/auto_runner.py
python scripts/auto_runner.py -k "test_search" --max-iterations 3
```

---

## 9. Common pytest-playwright Patterns

```python
# Navigate and wait
page.goto("https://en.wikipedia.org/wiki/Python")
page.wait_for_load_state("networkidle")

# Click and wait for navigation
with page.expect_navigation():
    page.get_by_role("link", name="Python (programming language)").click()

# Fill search and submit
page.get_by_role("searchbox").fill("Python")
page.get_by_role("button", name="Search").click()

# Assert URL changed
expect(page).to_have_url(re.compile(".*/wiki/Python.*"))

# Assert element visible
expect(page.locator("#firstHeading")).to_contain_text("Python")
```

---

## 10. Debugging Checklist

When a test fails:

1. Check the trace ZIP in `reports/traces/` (upload to trace.playwright.dev)
2. Check the screenshot in `reports/screenshots/`
3. Check `reports/logs/test.log` for the full error
4. Run `python scripts/auto_runner.py --no-fix` to get AI diagnosis without code changes
5. Check if the page URL matches expectations
6. Check if element is inside an iframe (need `frame_locator`)
7. Check if element is inside a shadow DOM (need `:host` or `pierce/` selector)
