# Visual regression testing

`checks/visual/` compares screenshots with committed baselines, pixel by pixel, and keeps the
evidence. The `visual-regression` plugin is a thin adapter over it. Playwright for Python has
no `to_have_screenshot` assertion (that lives in the Node test runner), so the engine
implements the same behaviour with Pillow.

## What it does

* **Capture.** `capture(page)` waits for fonts, disables animations and hides the caret, then
  takes a screenshot (`mask=[locators]` paints dynamic regions over).
* **Compare.** `compare(baseline, actual, pixel_threshold=0.1, max_diff_ratio=0.001)` decodes both
  PNGs (alpha flattened onto white), requires equal size (`size_changed` otherwise), counts the
  pixels whose YIQ colour distance exceeds `pixel_threshold` (pixelmatch's measure, so
  anti-aliasing noise is ignored), and reports `mismatch` when the differing share exceeds
  `max_diff_ratio`.
* **Evidence.** `reports/visual/<run_id>/<name>.actual.png` always, and `<name>.diff.png` (actual
  faded to grey, differing pixels red) when pixels differ. A `metric_visual_diff` row per
  comparison.
* **Baselines.** `visual_baselines/<browser>-<platform>/<name>.png`, committed. Fonts rasterise
  differently per OS, so a baseline is only compared on the platform that took it. A 0-byte or
  undecodable file is treated as missing.
* **Verdict:**

| What happened | Test helper | Plugin status |
|---|---|---|
| match | passes | `pass` |
| mismatch / size changed | `AssertionError` naming the diff image | `fail` |
| baseline missing | `AssertionError` ("no baseline: nothing compared") | `unknown` |
| baseline missing, update requested | baseline written, test skipped as `baseline_taken` | `skip` (`baseline_taken`) |
| no routes, Pillow or browser missing, route did not load | — | `unknown` |

An existing baseline is never overwritten by an update run; changing one goes through approval.

## How to use

```python
from checks.visual.assertions import assert_matches_baseline


def test_invoice_screen(page):
    page.goto(url)
    assert_matches_baseline(page, "invoice-screen", mask=[page.locator("[data-testid=clock]")])
```

Taking and approving baselines:

```bash
VISUAL_UPDATE=1 pytest tests/demo_examples/visual -m demo_ui        # writes missing baselines
pytest -p checks.visual.pytest_plugin --visual-update ...           # same, as an option
python -m checks.visual approve --run <run_id> [--name invoice-screen]  # reviewed actuals -> baselines
python -m checks.visual compare base.png actual.png --diff diff.png     # exit 0 / 1 / 2
```

Approval appends `{name, browser, platform, sha256, run_id, approved_at}` to
`visual_baselines/approvals.jsonl`; the PNG change still lands through a reviewed commit. CI
never approves.

Plugin context: `routes` (`{name: url}` or a list of URLs, named from their path),
`update_baselines`, `pixel_threshold`, `max_diff_ratio`, `baseline_root`, `browser`,
`metric_sink` / `record_metrics`, `dry_run`.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `pip install -r requirements-checks.txt` | not installed | provides `pillow==12.3.0` |
| `VISUAL_UPDATE` | unset | `1` writes missing baselines (skip, never pass) |
| `CHECKS_BROWSER_CHANNEL` | bundled Chromium | browser channel for the plugin |
| `pixel_threshold` | 0.1 | per-pixel colour tolerance, 0..1 |
| `max_diff_ratio` | 0.001 | share of pixels allowed to differ |

## Example

`tests/demo_examples/visual/test_visual_demo.py` (marker `demo_ui`):

* a fixture page captured as its own baseline matches when unchanged and mismatches (with a diff
  image) when one element changes colour — the positive control;
* the demo's login and customer-form screens match their committed baselines
  (`visual_baselines/chromium-darwin/` locally, `chromium-linux/` in CI);
* with a demo fault switched on, the fault banner changes and the login screen mismatches.

## Honest limits

Visual tests are brittle by nature (fonts, animations, dynamic data). Masks and a fixed
browser container reduce, not remove, the upkeep. Baselines grow the repository, so keep them to
key screens. Committed baselines exist for `chromium-darwin` and `chromium-linux`; the Linux set
is taken inside the CI image (`mcr.microsoft.com/playwright/python:v1.58.0-noble`, amd64), so a
Playwright bump in CI means retaking it there (the `visual-baselines` job). Approval is a human judgement:
the tool only proves that pixels changed.
