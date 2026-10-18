# Test plan: Sauce Demo

## Scope

- **In scope:** sign-in, the product list (sorting), the cart, and checkout up to the order
  confirmation, in Chromium.
- **Out of scope:** the deliberately broken users (`problem_user`, `error_user`,
  `visual_user`), product detail pages, the side menu, other browsers. Each leaves its
  defects undetected; the README lists them as next steps.

## Risks and priority

| Risk | Likelihood | Impact | Priority | Covered by |
|---|---|---|---|---|
| The order total is wrong | M | H | P1 | `tests/ui/test_checkout.py` |
| A valid user cannot sign in, or a blocked one can | M | H | P1 | `tests/ui/test_login.py` |
| An order cannot be completed | L | H | P1 | `tests/ui/test_checkout.py` |
| The cart badge disagrees with the cart | M | M | P2 | `tests/ui/test_cart.py` |
| Sorting by price is wrong | L | M | P3 | `tests/ui/test_inventory.py` |

## Automated vs exploratory

- **Automated:** the journeys above, every user class on sign-in, totals for one to three
  items.
- **Exploratory:** input validation on the checkout form (it found finding 1), layout on
  small screens, keyboard-only checkout.

## Entry and exit criteria

- **Entry:** the site answers and the documented demo users exist.
- **Exit:** every P1 test passes; every failure is either a finding or a fixed test.

## Environments and data

- **Target:** <https://www.saucedemo.com>, nightly. The site keeps the cart in the browser,
  so a fresh browser context per test is all the isolation the data needs.
- **Data:** the demo users and password the sign-in page publishes.

## Known limits

- The public site can change without notice; the nightly run opens an issue when it does
  instead of failing the main branch.
