# Test plan: W3C Before and After

## Scope

- **In scope:** home, news, tickets and survey, in the "before" and "after" versions, as
  they load, against axe-core's WCAG 2.1 A and AA rules.
- **Out of scope:** interactive states, manual checks (keyboard, screen reader), WCAG 2.2.
  See the README for the risk each leaves.

## Risks and priority

| Risk | Likelihood | Impact | Priority | Covered by |
|---|---|---|---|---|
| A clean result from a scanner that did not run, or ran on the wrong page | M | H | P1 | `test_before_page_shows_its_planted_violations`, `test_scanner_detects_a_planted_violation_on_the_target` |
| A serious or critical violation on a fixed page | L | H | P1 | `test_page_has_no_new_serious_violations` |
| The fixed version regresses towards the broken one | L | M | P2 | `test_after_page_has_materially_fewer_violations` |
| An excused violation is fixed and the excuse stays | L | L | P3 | `test_baseline_entries_still_reproduce` |

## Automated vs exploratory

- **Automated:** the four pages in both versions, on load.
- **Exploratory:** keyboard-only use of the survey and the navigation, a screen reader pass
  of the tickets table, and whether the alt texts the "after" version adds say anything
  useful (axe only checks that they exist).

## Entry and exit criteria

- **Entry:** every "before" page shows its planted violations. If not, stop: the scanner
  or the page is wrong, and no "after" result is evidence.
- **Exit:** no new serious or critical violation on "after", and no stale baseline entry.

## Environments and data

- **Target:** <https://www.w3.org/WAI/demos/bad/>, nightly. Static pages; no data to own or
  clean up.
- **Data:** the page list and planted rules in `a11y_w3c_bad.a11y.pages`.

## Known limits

- The host sits behind a CDN that challenges headless browsers; the suite identifies
  itself with its own user agent. If that stops working, the identity check fails loudly.
