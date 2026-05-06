---
seed: true
feature: wiki_search
---

# Seed Spec — Wikipedia Search

This is a bootstrap spec. It describes the environment setup and one basic
smoke scenario that existed before `web_discovery` ran automated planning.
`core/ai/TestGenerator` uses it as the starting reference for generating
the initial `ui/tests/test_search.py` test file.

---

## Environment Setup

These steps apply to every scenario in this spec and in `wiki-search-flows.md`.
They are not a test themselves — they are the shared precondition block that
the Generator inlines as a pytest fixture or `conftest.py` setup.

### Steps
1. Navigate to `https://en.wikipedia.org/wiki/Main_Page`
2. If the cookie-consent banner is visible, click the "Accept all" button
3. Wait for the search input (`#searchInput`) to be visible

### Expected
- The Wikipedia main page is loaded (`page.url` contains `wikipedia.org`)
- The search input element is present and interactable

---

## Scenario basic search loads results page

### Preconditions
- Browser is on the Wikipedia main page
- Cookie consent has been accepted (or is not shown)
- Search input is visible and focused

### Steps
1. Click the search input field
2. Type the search term `Python programming language`
3. Press Enter or click the search submit button
4. Wait for the results page to load (`networkidle`)

### Expected
- The page URL contains `/wiki/Python` or `/w/index.php?search=`
- The page `<h1>` is visible and contains the search term or a related title
- No error message or "did not match any results" text is present

### Tags
`smoke` `seed`
