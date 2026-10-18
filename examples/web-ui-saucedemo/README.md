# Sauce Demo

> Sign-in, cart and checkout on the public Sauce Demo shop.

A worked example of the `web-ui` template: the skeleton is rendered from it unchanged
(the files listed in [.scaffold-manifest](.scaffold-manifest)), the tests and page objects
are written for this site.

## Run it

```bash
make test        # installs dependencies and Chromium on first run, then runs the suite in parallel
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`. Or, with only Docker:
`docker compose run --rm tests`. Expected runtime: about 15 seconds on a laptop.

`make test-offline` runs the page-object tests against a local copy of the sign-in markup,
with no network. The live tests run against <https://www.saucedemo.com> every night, never
on a pull request: a third-party outage is not a defect in this code.

`make test-local` runs the whole suite against Sauce Labs' `sample-app-web`, the source of
the public shop, built from a pinned commit ([local/sample-app-web](local/sample-app-web/Dockerfile))
and served on `localhost:8301`; this is what a pull request runs. The local server sends a
Content-Security-Policy that keeps the browser on its own origin, so the app's web fonts
and error reporting are blocked rather than fetched. Prerequisites: Docker, uv and GNU make.

## Decisions and trade-offs

| Decision | Alternatives considered | Why | Cost accepted |
|---|---|---|---|
| Page objects plus a header component | Screenplay; plain functions | Locators live in one place per screen; a test reads as what the shopper does | One more layer to navigate |
| Locators on `data-test` ids only | CSS classes, text | The site ships stable test ids on every element the tests touch | Elements without an id are out of reach until the site adds one |
| Web-first assertions only, no sleeps | Explicit waits, `sleep` | `performance_glitch_user` answers slowly on purpose and still passes | A wrong expectation fails after the timeout, not at once |
| Fresh browser context per test, run with `-n auto` | One signed-in browser for all tests | The cart lives in the browser: a shared context would leak items between tests | Each test signs in again (about a second) |
| Amounts compared as `Decimal` | Floats | `$39.98` must equal the sum of `$29.99` and `$9.99` exactly | Parsing the label text |

## Scope

**In scope:** sign-in for every user class the site documents, the cart badge, sorting by
price, and checkout arithmetic up to the order confirmation.

**Out of scope**, and the risk each leaves:

- `problem_user`, `error_user` and `visual_user`: their deliberate defects (wrong images,
  failing buttons, shifted layout) go undetected.
- Browsers other than Chromium: rendering differences in Firefox and WebKit go undetected.
- Product detail pages and the side menu.

## What the tests cover

| Risk | Test |
|---|---|
| A valid user cannot sign in, or a blocked one can | `tests/ui/test_login.py::test_sign_in_outcome_matches_the_user` (6 cases) |
| The product list is reachable without signing in | `tests/ui/test_login.py::test_product_list_is_closed_to_a_visitor_who_has_not_signed_in` |
| The cart badge disagrees with the cart | `tests/ui/test_cart.py` |
| The order total is wrong | `tests/ui/test_checkout.py::test_checkout_total_includes_tax_for_multiple_items` |
| An order cannot be completed | `tests/ui/test_checkout.py::test_order_is_confirmed_after_finish` |
| Sorting by price is wrong | `tests/ui/test_inventory.py` |
| The page objects drifted from the markup | `tests/unit/test_login_page.py` (offline) |

## How to read the report

- **Locally:** `reports/report.html`, one self-contained file.
- **A failed test** leaves a Playwright trace, a screenshot and a video in
  `reports/test-results/`. Open the trace with
  `uv run playwright show-trace reports/test-results/<test>/trace.zip`.

## Findings

| # | Finding | Severity | Steps to reproduce | Evidence |
|---|---|---|---|---|
| 1 | The checkout form accepts any postal code, including one with no digits | Low | Check out with postal code `ABC` | The overview opens; `test_a_postal_code_without_digits_is_refused` (expected to fail until fixed) |

## What I would do next

1. Cover `problem_user` and `error_user`: they are the site's own regression targets.
2. Add Firefox and WebKit once the Chromium suite has been stable for a month.

## Project layout

```text
src/web_ui_saucedemo/
  pages/        login, inventory, cart, checkout
  components/   header with the cart badge
  data/         users and products the tests use
  money.py      "$43.18" -> Decimal("43.18")
tests/
  ui/           live tests against www.saucedemo.com
  unit/         offline tests: settings, the login page object on local HTML
  fixtures/     the sign-in markup for the offline tests
```
