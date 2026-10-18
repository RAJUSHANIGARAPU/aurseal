# Accessibility testing

`checks/a11y/` runs [axe-core](https://github.com/dequelabs/axe-core) inside a real
Playwright page and judges the result against a WCAG policy. The `accessibility-a11y` plugin
is a thin adapter over it.

## What it does

* **Scan.** `scan(page, policy)` injects axe-core (bundled by the `axe-playwright-python`
  package, axe 4.12.1) and runs it with the policy's WCAG tags. The report lists each violated
  rule with impact, WCAG tags, node count and target selectors, plus how many rules axe actually
  evaluated (`rules_run`).
* **Policy.** `A11yPolicy(min_impact="serious", tags=("wcag2a", "wcag2aa"), disabled_rules=())`:
  `serious` and `critical` violations block; `moderate` and `minor` are reported only. A missing
  impact counts as the most severe.
* **Open issues, per route and rule.** Pre-existing violations are never allow-listed silently.
  They go in a `*.manifest.json` file (the verdict layer's format): `template` is the
  route path, an issue's `finding` the axe rule id. A known violation is excused only on its own
  route, and asserted to still occur there; when it stops, the check fails with
  `KNOWN DEFECT CLEARED` until the entry is removed. A rule that did not run cannot "clear".
* **Verdict** (`checks._outcome`):

| What happened | Status |
|---|---|
| every route scanned, no unexplained blocking violation | `pass` |
| an unexplained blocking violation, or a known one that cleared | `fail` |
| no routes, axe or browser unavailable, a route that did not load, a scan that ran no rules | `unknown` |
| dry run | `skip` |

* **Evidence.** `metric_a11y_scan` (one row per scanned route, so a clean scan still shows it
  ran) and `metric_a11y_violation` (one row per violated rule, with the known-defect ticket).

## How to use

Inside any UI test:

```python
from checks.a11y.assertions import assert_accessible, load_known

def test_checkout_page_is_accessible(page):
    page.goto(url)
    assert_accessible(page, manifest=load_known("tests/a11y/a11y.manifest.json"))
```

Standalone (starts its own browser):

```bash
python -m checks.a11y --route http://127.0.0.1:8765/ui --route http://127.0.0.1:8765/login \
    --min-impact serious --known tests/demo_examples/a11y/a11y.manifest.json
# exit 0 = clean, 1 = violation or cleared known defect, 2 = could not scan
```

Plugin context: `routes` (absolute URLs, required), `min_impact`, `tags`, `disabled_rules`,
`open_issues` (manifest file or directory), `browser`, `metric_sink` / `record_metrics`,
`dry_run`.

Manifest entry:

```json
[{"template": "/ui/subscriptions",
  "open_issues": [{"issue": "BUG-171", "summary": "a11y",
                   "notes": "the list filter input has no accessible name",
                   "seen_in": "axe rule 'label' on [data-testid=filter-input]",
                   "finding": "label"}]}]
```

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `pip install -r requirements-checks.txt` | not installed | provides `axe-playwright-python==0.1.8` |
| `AXE_SCRIPT_PATH` | unset | use this `axe.min.js` instead of the package's copy (fallback when the package cannot be installed) |
| `CHECKS_BROWSER_CHANNEL` | bundled Chromium | browser channel for the CLI/plugin (`chrome` locally) |
| `METRICS_DIR` | `reports/metrics` | where metric rows go |
| `CHECKS_REQUIRE_TOOLS` | unset | in tests: `true` makes a missing axe a failure, not a skip |

## Example

`tests/demo_examples/a11y/test_a11y_demo.py` (marker `demo_ui`):

* positive controls: a known-bad page reports `image-alt` and `label`; a known-good page is clean
  with rules actually run;
* the demo's key screens pass the default policy;
* a detector pair on `/ui/subscriptions` with the demo fault `screen_filter_unlabelled`: on, the
  unlabelled filter reproduces (`BUG-171`); off, the detector clears;
* the manifest scoped to route + rule: with the fault on the known violation is excused and
  asserted; with it off the entry reports `KNOWN DEFECT CLEARED`.

## Honest limits

Automated rules catch only part of WCAG. Keyboard flow, focus order and whether alt text is
meaningful still need a person. A green scan means "no automatically detectable violations".
