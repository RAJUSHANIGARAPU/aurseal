| Risk | Test |
|---|---|
| A new serious or critical WCAG violation ships | `tests/a11y/test_pages.py::test_page_has_no_new_serious_violations` |
| A filed violation is fixed but stays excused | `tests/a11y/test_pages.py::test_baseline_entries_still_reproduce` |
| The scanner cannot run on the target (CSP, wrong page) and reports a clean page | `tests/a11y/test_pages.py::test_scanner_detects_a_planted_violation_on_the_target` |
| The scanner misses violations it should find | `tests/unit/test_scanner.py` (offline, local HTML fixtures) |
| The baseline excuses more than it should | `tests/unit/test_baseline.py` |

Automated rules find only part of what WCAG asks for. Keyboard-only use, focus order and a
screen reader pass are checked by hand: _(record what you checked, on which pages, and what
you found, under Findings)_.
