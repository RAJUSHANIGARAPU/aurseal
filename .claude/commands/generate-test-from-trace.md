# generate-test-from-trace

Generate a complete pytest + Playwright test file from a trace ZIP.

## Usage

```
/generate-test-from-trace [trace.zip] [page_name]
```

Example: `/generate-test-from-trace reports/traces/test_search_20250423.zip wiki_search`

## Steps

1. Find the trace:
   ```bash
   ls -t reports/traces/*.zip | head -3
   ```

2. Generate the test:
   ```python
   from core.ai.test_generator import TestGenerator
   from pathlib import Path

   gen = TestGenerator()
   code = gen.generate_from_trace(Path("reports/traces/<trace>.zip"), "wiki_search")
   Path("ui/tests/test_wiki_search.py").write_text(code)
   print(code)
   ```

3. Or generate a page object:
   ```python
   code = gen.generate_page_object(
       "WikiSearchPage",
       "https://en.wikipedia.org/wiki/*",
       "Wikipedia search and article navigation"
   )
   Path("ui/pages/wiki_search_page.py").write_text(code)
   ```

4. Verify generated code compiles:
   ```bash
   python -m py_compile ui/tests/test_wiki_search.py && echo OK
   ```

5. Run the generated test:
   ```bash
   pytest ui/tests/test_wiki_search.py -v
   ```

## Framework conventions (enforced in generated code)

- Inherit from `core.base_page.BasePage`
- Use `expect()` not `assert` for Playwright assertions
- Locator priority: `data-testid` > `aria-label` > `role` > `text`
- Never `time.sleep()` — use `page.wait_for_load_state()` or `expect().to_be_visible(timeout=N)`
