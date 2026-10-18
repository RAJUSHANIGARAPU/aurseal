# W3C Before and After

> WCAG 2.1 AA scans of the W3C Before and After Demonstration, with the inaccessible version
> as a built-in positive control.

A worked example of the `a11y` template: the skeleton is rendered from it unchanged (the
files listed in [.scaffold-manifest](.scaffold-manifest)); the page list, the user agent
and the before/after tests are written for this site.

The [Before and After Demonstration](https://www.w3.org/WAI/demos/bad/) publishes the same
four pages twice: an inaccessible "before" version and a repaired "after" version. That
makes it an honest target: the gate runs on "after", and "before" proves the scanner can
see what it is supposed to see.

## Run it

```bash
make test        # installs dependencies and Chromium on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`. Or, with only Docker:
`docker compose run --rm tests`. Expected runtime: about 15 seconds.

`make test-offline` scans the local fixtures in `tests/fixtures/static/` with no network.
The live tests run against <https://www.w3.org/WAI/demos/bad/> every night, never on a pull
request: a third-party outage is not a defect in this code.

`make test-local` is what a pull request runs, and it is **not** a scan of the demo. The
demo's pages come under the W3C document licence, which allows unmodified copies with a
notice and no derivatives; that is not a clear enough grant to vendor a whole site for a
test server without asking, so they are not copied here. Instead nginx on `localhost:8304` answers the demo's page paths with this
example's own fixtures ([local/nginx.conf](local/nginx.conf)), and the suite runs without
`test_before_after.py`, which asserts on the demo itself. It proves the scanner, the
baseline and the report work over HTTP; only the nightly run judges the demo.
Prerequisites: Docker, uv and GNU make.

## Decisions and trade-offs

| Decision | Alternatives considered | Why | Cost accepted |
|---|---|---|---|
| The "before" pages as a positive control, per page, on the rules each one breaks | Trusting a clean "after" scan | A scan that finds nothing proves nothing until the same scanner, on the same site, finds the planted faults | Eight scans instead of four |
| A page-identity check before every scan (status and title) | Scanning whatever loaded | The site's CDN answers a headless browser with a bot challenge; scanned, that page reports one critical finding and looks plausible | Breaks if the demo is retitled |
| A stable, identifying user agent | The browser default; a desktop browser's string | The default gets the challenge; an honest name gets the page and tells the host who is calling | Depends on the host keeping that policy |
| Serious and critical fail, moderate and minor are logged | Fail on every finding | Matches the gate in the template | Lower-impact findings are only seen in the log and `axe.json` |
| "Materially fewer" = at most a quarter of the blocking findings, and none of the planted rules | Zero findings on "after" | Rule updates in axe can add findings to both versions; the comparison stays meaningful | A partial regression under a quarter passes this test (the gate still catches it) |
| Pages not vendored | Copying the demo into `tests/fixtures/` | The demo's licence for redistribution was not confirmed; the template's own fixtures cover the offline tier | Pull requests cannot scan the real pages |

## Scope

**In scope:** the four pages (home, news, tickets, survey) in both versions, as they load,
against the WCAG 2.1 A and AA rules axe-core implements.

**Out of scope**, and the risk each leaves:

- Interactive states: the survey posts to the server and the menus are hover-driven; errors
  shown after submitting are not scanned.
- What automated rules cannot judge: the quality of alt text, reading and focus order.
- WCAG 2.2: run with `APP_WCAG_TAGS=wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22aa`.

## What the tests cover

| Risk | Test |
|---|---|
| The scanner misses violations, or scans the wrong page | `tests/a11y/test_before_after.py::test_before_page_shows_its_planted_violations` |
| The fixed pages are not materially better than the broken ones | `test_after_page_has_materially_fewer_violations` |
| A new serious or critical violation on a fixed page | `tests/a11y/test_pages.py::test_page_has_no_new_serious_violations` |
| A filed violation is fixed but stays excused | `tests/a11y/test_pages.py::test_baseline_entries_still_reproduce` |
| axe cannot run on this site at all | `tests/a11y/test_pages.py::test_scanner_detects_a_planted_violation_on_the_target` |
| The scanner or the baseline rules are wrong | `tests/unit/` (offline) |

Not checked by hand in this example: a keyboard-only pass and a screen reader pass are the
first items under "What I would do next". In a real assignment, record them here.

## How to read the report

- **Locally:** `reports/report.html`, one self-contained file.
- **Every scan** writes its findings to `reports/artifacts/<test>/`, one `axe-*.json` per
  page, and a failed scan prints them as a table: rule, impact, element and the fix.
- **A failed test** also leaves a Playwright trace and a screenshot in `reports/test-results/`.

## Findings

| # | Finding | Severity | Steps to reproduce | Evidence |
|---|---|---|---|---|
| 1 | The "before" pages fail four to five WCAG 2.1 A/AA rules each, 40 to 45 blocking findings per page (missing alt text, no page language, unnamed links and selects, low contrast, unlabelled fields) | Critical, by design | Scan `before/home.html` | `axe-before-home.html.json`; `test_before_page_shows_its_planted_violations` |
| 2 | The "after" pages have no WCAG 2.1 A/AA findings at all | (none) | Scan `after/*.html` | `test_page_has_no_new_serious_violations` |
| 3 | A headless browser's default user agent gets a CDN bot challenge (HTTP 403) instead of the demo | Test infrastructure | Load any demo page from headless Chromium | Without the user agent in `tests/a11y/conftest.py`, all 17 live tests fail at the identity check |

`a11y-baseline.json` is empty: nothing on the "after" pages needed excusing.

## What I would do next

1. A keyboard-only walkthrough of the survey and the navigation, recorded as a Playwright
   test, and a screen reader spot check of the tickets table.
2. Scan page states: the home page with the navigation focused by keyboard.
3. Ask W3C whether unmodified copies of the demo may be served from this repository; if
   so, the local lane could scan the real pages instead of the fixtures.

## Project layout

```text
a11y-baseline.json   known violations, each with its issue (empty)
src/a11y_w3c_bad/
  a11y/
    scanner.py  injects axe-core, runs it with the configured tags, returns findings
    baseline.py the known-violations file and the verdict: new, still known, fixed
    pages.py    the four pages, before and after, and the rules each "before" page breaks
  settings.py   APP_* environment settings
tests/
  a11y/         live scans: the gate on "after", the control on "before"
  unit/         offline: the scanner on local HTML, the baseline rules
  fixtures/     static HTML with planted violations, and its fixed version
```
