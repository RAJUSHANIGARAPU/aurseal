# Demo UI examples

Browser examples that drive the screens of the in-repo demo service
(`demo_sut`, see [demo-sut.md](demo-sut.md)) through the framework's page objects
and UI components. They show each pattern on a system anyone can run, with its
fault switches as the defects to find.

* **Tests:** `tests/demo_examples/ui/`. Every browser test carries
  `@pytest.mark.demo_ui` and belongs to the browser lane; helpers and guards that
  need no browser are unmarked and run offline.
* **Page objects:** `ui/pages/demo/` on top of `core.base_page.BasePage`.
  Locators live in `ui/locators/demo_locators.json` (test ids, `data-action`
  attributes, labels and roles; no CSS, no XPath). An entry may be a template:
  `{"type": "testid", "value": "error-{field}"}` is resolved with
  `resolve("price.field_error", field="tax_rate")`.
* **Flow:** `ui/flows/demo_subscription_flow.py` drives the wizard end to end
  (Parties, Price, Confirm) and keeps every step's submit outcome.
* **Evidence:** the `demo_page` fixture (`tests/demo_examples/ui/conftest.py`)
  attaches `core.browser_evidence.BrowserEvidence` to the context and writes it to
  the test's `user_properties` as `browser_evidence`; `demo_control` adds
  `demo_faults`, the fault set active during the test.

## Running them

```bash
# browser lane: the demo starts in-process per xdist worker, Chrome via pytest-playwright
pytest tests/demo_examples -m demo_ui -n auto

# offline lane: everything else, no browser
pytest tests/ -n auto -m "not demo_ui"
```

| Setting | Default | Effect |
|---|---|---|
| `DEMO_SUT_URL` | unset | use an already-running demo instead of starting one (run with `-n 0`) |
| `--browser-channel` | none (CI passes `chrome` on the browser-lane commands) | the browser pytest-playwright launches |
| `--headed` | off | watch the run |

Each test freezes the demo clock at `2026-03-10T09:00:00Z` (`demo_control.freeze`)
and seeds its own data over the API; the shared session database is never
assumed empty.

## Closure precondition tick

**What it does.** The closure screen refuses to confirm until the "may be closed on this
date" box is ticked. The example confirms once unticked and proves, with
`submit_tracking_sent`, that **no request left the browser**; the named message and a
visible alert are on screen. After the tick the confirm answers 200 and the subscription
is cancelled.

**Use.** `DraftScreen.tick_precondition()`, `precondition_error()`,
`confirm(action, timeout_ms=...)`.

```python
blocked = draft.confirm("confirm_cancel", timeout_ms=1500)
assert not blocked.sent and draft.precondition_error()
draft.tick_precondition()
assert draft.confirm("confirm_cancel").outcome.status == 200
```

Test: `test_cancellation_ui.py::test_closure_is_blocked_until_the_precondition_is_ticked`.

## Credit payee resolution

**What it does.** A closure credit goes to the first *complete* (account, holder) pair
among: the subscription's own direct-debit account, then the customer's payout account.
`support.first_complete(candidates)` skips a half-filled candidate rather than using it. The
example closes a company subscription through the screen and checks the stored
`closure.payee_account` is the one the rule picks, for a subscription paid by card (the
customer's account wins) and one paid by direct debit (its own account wins).

```python
source, payee = support.first_complete(
    [
        ("subscription", {"account": view["payment_method"].get("account"), "holder": holder}),
        ("customer", {"account": customer["payout_account"], "holder": customer["display_name"]}),
    ]
)
```

Test: `test_cancellation_ui.py::test_the_credit_goes_to_the_first_complete_payee[payout-account|debit-account]`.

## Quote to confirmed in the wizard

**What it does.** Drives the wizard for an unverified company customer: reseller and
customer picked by reference number in the dialog (`reference_picker.pick`, default list
first, search second), the screening run for the unverified customer, plan, workspace type
and data region chosen in native selects (`dropdown.select_native`, read back), the start
date set (`set_native_date`), a net amount, tax rate and commission typed with read-back
(`value_entry.fill_amount_and_verify`), the price calculated, the quote generated and the
subscription confirmed. The screen's amounts are compared with the captured calculation
response and the stored subscription.

```python
run = SubscriptionWizardFlow(page, demo_sut.base_url).create(
    WizardInput(
        partner["number"],
        customer["number"],
        "Team plan (TEAM)",
        date(2026, 3, 1),
        workspace_type="TRAINING",
        data_region="EU-NORTH",
        net="45.00",
        tax_rate="20",
        commission="10",
    )
)
assert run.screening_status == "verified"
assert run.amounts["gross"] == Decimal("54.00")
```

Test: `test_quote_and_ledger_ui.py::test_company_quote_to_confirmed`.

## Ledger grid structure (fresh and deep-linked)

**What it does.** Reads the detail page's ledger grid
(`SubscriptionDetail.ledger()`, cells by `data-col`, amounts parsed) and checks it
row by row against `GET /api/subscriptions/{id}/transactions`, then structurally
with `oracles.ledger_checks`: first row `NEW`, every `NEW` total positive and
state set, net + tax = total where no fee applies, monthly periods contiguous and
one month long, and the grid total at least half a year's gross. Run once after
creating the subscription in the wizard and once by URL straight to the detail page.

Test: `test_quote_and_ledger_ui.py::test_ledger_grid_structure[fresh|deep-link]`.

## Closure routes: fresh, reuse, deep-linked draft

**What it does.** The same closure reached three ways: a just-seeded and booked company
subscription closed from its detail page; a subscription reused from the session's pool
(`support.reusable`, newest active one with a booked period, seeded when the pool is
empty); and a draft created over the API and opened by URL, which skips the UI creation
step. Each confirm is a tracked submit, and the API must hold exactly **one** CANCEL
transaction: a resend would leave two. The grid's CANCEL row equals the stored one.

Test: `test_cancellation_ui.py::test_company_closure_routes[fresh|reuse|deep-link]`.

## Status, form-error and document assertions

**What it does.** On a healthy detail page: the status pill, no visible alert and an empty
error banner. The account-opened letter is downloaded through the browser
(`SubscriptionDetail.download_document_for`, Playwright download: suggested name and
bytes), read with `oracles.pdf_text`, and its *Starts on* and *Term ends* must equal the
stored dates. The day before and the day after are checked to be absent, so a time-zone day
shift is named as such. Then a tax rate of 150 on the price step must show the catalogue
message on exactly that field.

Test: `test_detail_assertions_ui.py::test_status_errors_and_documents` (skips the PDF body
check only when `pypdf` is missing).

## Future-dated plan change on a deep-linked draft

**What it does.** A change draft is created over the API and opened by URL; the effective
date is set to 15 April, the monthly net is raised by 4.50 and an add-on is ticked. After
confirming, the detail page still shows version 1 (the new version is *scheduled*), the
API's version 2 carries the add-on, and the pro-rata `CHANGE` row (16 of April's 30 days)
is on the grid with the amount an independent day count gives.

Test: `test_change_ui.py::test_a_future_plan_change_on_a_deep_linked_draft`.

## Changing the data region, and the safe-retry rule

**What it does.** Part one changes the subscription's data region on a deep-linked change
draft and reads it back on the detail page and the API. Part two shows
`ui.components.resend_rule`: with the demo fault `create_throttled_every_other` on, two
wizard runs always meet one throttled create; a 429 is a refusal that stored nothing, so it is resent and
the customer ends up with exactly two subscriptions. Part three lets the service store a
create while the browser sees a gateway 504 (`page.route` fetches, then fulfils 504):
`may_resend` is false, the test does not resend, and exactly one subscription exists.

| Answer | Sent? | `may_resend` |
|---|---|---|
| never sent | no | yes |
| 4xx (refused, nothing written; the example meets a 429) | yes | yes |
| 2xx, 5xx, 504, no answer | yes | **no** |

Test: `test_change_ui.py::test_region_change_and_the_safe_retry_rule`.

## Billing contact change read back

**What it does.** Changes the billing contact on a deep-linked change draft (field located
by its label, value read back), confirms, and reads the value on the detail page by exact
test-id path (`read_field(path="billing-contact")`, `via == "test-id"`), where it is static
text (`field_is_read_only` is true). The API stores name and e-mail as typed.

Test: `test_change_ui.py::test_billing_contact_change_reads_back_on_the_read_only_overview`.

## Closure reason kept across a validation error (detector)

**What it does.** A reason is chosen, then the end date is cleared so the confirm is blocked
by a field error before anything is sent; the date is set again and the confirm goes out.
The oracle requires the screen to still hold the chosen reason and the stored closure to
carry it. With the demo fault `form_resets_select_after_error` on, the select falls back to
its first option after the error and the pair reads `reproduces` (`DEMO-118`). The control
closes another subscription without an error in between: the picked reason is what is
sent, fault or not.

Test: `test_cancellation_ui.py::test_reason_survives_a_validation_error[fault-on|fault-off]`.

## Money-path drivers

**What it does.** Three flows that move money, each checked against `oracles.daycount`
(ACT/ACT, the demo's `actual/period` setting) on the API and on the grid:

* **partial credit**: closing mid-period credits the unused share of the booked month
  (`candidate_refund`, `within_pro_rata_bounds`);
* **price delta**: a raise from mid-period books the pro-rata delta (16 of March's 31 days);
* **payment change**: a direct-debit account set on a change draft over the API is
  confirmed in the UI; the stored name check records the result (`NO_MATCH` here, via the
  `account_check_mismatch_once` setting) and the holder, and no money moves.

Each run writes its evidence line to the test's properties (`money_path`).

Test: `test_money_paths_ui.py::test_money_path_drivers[partial-credit|price-delta|payment-change]`.

## Invoice delivery rule on the Parties step

**What it does.** `SubscriptionWizard.observe_address_block(case, message)` reads the billing
address block, submits the Parties step while capturing status, inline errors and alerts,
and returns `StepObservations`; the page object never judges.
`verification.outcome_classifier.step_outcome` names the outcome, and the expected state
comes from a declared matrix: a customer with an e-mail may choose e-mail delivery, a
customer without one may choose the portal, and e-mail delivery without an address is
blocked with *Invoices by e-mail need an e-mail address.* The control is that the billing
address block was filled. An abstention (step never rendered, block absent) is reported
through `EvidenceVerdict.no_verdict`, never as a pass.

Test: `test_customer_form.py::test_the_delivery_rule_on_the_parties_step[...]`.

## Individual closure routes, with a stale draft in the way

**What it does.** The same three routes as the company closure, for an individual
customer. On the fresh route an abandoned change draft blocks the subscription: the screen
withholds the action and offers "Continue the open draft".
`SubscriptionDetail.start_discarding_a_stale_draft(action)` opens that draft, discards it on
its own screen and starts the action again (it does the same when the start answers 409).

Test: `test_cancellation_ui.py::test_individual_closure_routes[fresh|reuse|deep-link]`.

## Individual plan change, path recorded

**What it does.** A deep-linked change on an individual subscription: an add-on switched on
from a future date and the net price typed with read-back. The steps the draft listed,
whether the price was typed and whether the change was confirmed are written to the
test's properties (`change_trail`) and asserted.

Test: `test_change_ui.py::test_an_individual_plan_change_records_the_path_it_took`.

## Individual subscription assertions

**What it does.** The detail page object's assertion helpers on an individual
subscription: id from the URL, number issued, current version, the four page sections
shown (`section_visible`), no alerts, `status_contains`, plan kind; after a closure
confirmed over the API, `is_cancelled` and exactly one `CANCEL` row (`ledger_kinds`).

Test: `test_detail_assertions_ui.py::test_individual_subscription_assertions`.

## Customer maintenance page object

**What it does.** `CustomerMaintenance` (`ui/pages/demo/customer_form.py`) creates and
edits a customer: type and display name, one billing address (the home country is
preset), e-mail, phone and payout account. The example creates a company customer,
reopens it and reads every value back, edits one address field and clears the phone, and
proves the other address fields are untouched, on screen and over the API.

```python
form = CustomerMaintenance(page, base_url).open_new()
form.fill_identity(display_name="Harbourline Studio", customer_type="COMPANY")
form.set_address(line1="2 Pier Steps", postal_code="PS 12", city="Gullhaven")
form.set_email("studio@harbourline.example")
form.save()
```

Test: `test_customer_maintenance.py::test_create_edit_and_read_back_a_customer`.

## Price step page object: inputs and dropdowns

**What it does.** `PriceStep` enters amounts with numeric read-back, clears a
field, types a literal as-is, reads raw values and editability; the wizard's
native selects are chosen and read back; the payment frequency on a change draft
reads as read-only.

Test: `test_price_page_ui.py::test_price_inputs_and_dropdowns`.

## Scoped amount reads, field errors and alerts

**What it does.** `price-net` (the input) and `price-net-normalised` (the computed
output) share a name stem; reading by exact test-id path returns the input only.
Computed amounts are read by their own paths. A refused calculation shows one
named error per offending field (`field_errors()`), exactly two new inline errors
and the page's error banner.

Test: `test_price_page_ui.py::test_scoped_amount_reads_errors_and_alerts`.

## Reference autofill

**What it does.** Picking a customer in the reference dialog makes the screen fetch the
record and fill the billing address block; the block must equal the address in the fetched
response. Typing the customer number directly fills the same block; an unknown number fills
nothing and says so.

Test: `test_reference_autofill.py::test_reference_pick_and_direct_entry_autofill_the_address`.

## Deep link to the price step

**What it does.** A subscription seeded over the API up to plan selection (status
`draft`) is opened straight on `/ui/subscriptions/{id}/step/quote`, skipping the
wizard. The step marker reads *Price*, the plan's defaults are prefilled and the
calculated amounts match the stored price.

Test: `test_deep_link_quote_step.py::test_deep_link_to_the_price_step`.

## Retired admin entry

**What it does.** The admin cards no longer offer *Archived lists*. The sibling entries
must render first (positive control); then the retired link must be absent from the final
screen, recorded with `EvidenceVerdict.proved`. This checks the final screen only; to prove
the entry never appeared even for a moment during rendering, wrap the flow in
`ui.components.absence_watch` (see [ui-components.md](ui-components.md)). The retired page
still answers by URL with its breadcrumb.

Tests: `test_admin_screen.py::test_retired_entry_absent_with_control`,
`test_admin_screen.py::test_retired_page_still_reachable_by_url`.

## Individual plan: create, confirm, cancel through the UI

**What it does.** Partner and customer are provisioned over the API and picked in
the wizard by reference number; the subscription is confirmed, its twelve monthly
transactions checked over the API (the first one carrying the setup fee), March
booked, and the subscription closed through the detail page; the later periods are
voided.

Test: `test_wizard_flows.py::test_individual_plan_create_confirm_cancel_via_ui`.

## Ledger accounts: Edit follows the managed flag

**What it does.** For every row the row menu is opened and *Edit* must be
disabled exactly when `GET /api/accounts` says `managed`; the edit screen of an
editable account shows its number read-only and its current name editable.

Test: `test_accounts_screen.py::test_edit_disabled_exactly_for_managed_rows`.

## Integrations: optional base URL

**What it does.** The services response the screen is built from is captured
(`ResponseCapture`); every service listing `baseUrl` among its optional
parameters must render it visible, editable, not required, not invalid, labelled
optional and with a hint, while a required parameter of the same form reads as
required (control).

Test: `test_integrations_screen.py::test_optional_url_field_not_required`.

## Message and document downloads

**What it does.** After a booking hands a message to the outbox and its files are
generated, the detail page offers *Download message* and *Download document*.
Both are clicked as real Playwright downloads (`SubscriptionDetail.download_message`,
`download_document`) and must deliver bytes and a suggested file name; the
document must be a PDF. Recorded with `EvidenceVerdict.proved`.

Test: `test_downloads.py::test_message_and_document_downloads_have_bytes_and_name`.

## Background job with polling

**What it does.** *Generate messages* answers 202 with a job id; the page then
polls the job. `SubscriptionDetail.generate_messages()` returns the POST outcome
and every poll URL in order: the first poll must target that job id, every poll
the same job, the job ends `DONE` and the message moves from `NEW` to
`TO_BE_SENT` after the page reloads.

Test: `test_background_job.py::test_generate_runs_as_polled_job`.

## Front-end smoke

Five read-and-compare checks over the list, detail, picker and task screens
(`ui/pages/demo/lists.py`):

* **Version selector**: on a subscription with two versions, choosing version 1
  moves the URL to `?version=1`, the captured version GET answers 2xx with a
  non-current status, the panel shows version 1 and no lifecycle action is offered.
* **Reference picker**: the dialog's row count equals the search response; clicking
  the first row closes the dialog, shows its number and offers *Change*; the step
  then submits (or is refused with a positive signal when that customer's billing
  address block cannot be filled).
* **Task list vs API**: row count and the first row's cells equal `GET /api/tasks`;
  the row menu's *Open* lands on the task's subscription version.
* **Filter chip and keyset paging**: with a page size of 2, filtering on a unique
  term sends a search whose body carries the term; every page's titles contain it,
  *Next* sends an `after` cursor, the pages together hold exactly the five seeded
  subscriptions without duplicates, and *Previous* (a `before` cursor) returns the
  previous page.
* **Post-deploy click-through**: every side-menu entry is shown, Admin has cards,
  the first list row opens a detail page whose GET answers 200 with a status pill,
  and a task created through the form answers 201 and is listed `OPEN`.

Tests: `test_frontend_smoke.py::test_version_selector_loads_older_version`,
`::test_reference_picker_matches_search_results`, `::test_task_list_matches_api`,
`::test_search_chip_filters_and_keyset_pages`, `::test_post_deploy_menu_click_through_submit`.

## Login smoke

**What it does.** With the demo's `auth_required` setting on, a screen without a
session gets a 401 from its API call and shows the error banner (control); a
wrong password stays on the login page with its message and no session cookie;
the right one leaves the login page, sets the cookie, and the same screen's API
call answers 200. Page object: `ui/pages/demo/login.py::DemoLoginPage`.

| Setting | Value in the test |
|---|---|
| `auth_required` (demo setting) | `true`, restored by `demo_control` |
| user / password | the demo's throwaway `demo-clerk` / `demo-password` |

Test: `test_login_smoke.py::test_login_smoke_preset`.

## A bad postal code on its field

**What it does.** Postal codes are free-form within limits (letters, digits, spaces and
hyphens, starting with a letter or digit). The form run types `51/7`, saves, and expects a
422 with exactly the catalogue message on the postal-code field, an API-status rejection
signal (`oracles.rejection`) and the same message in the response's `fields`. Corrected to
`TQ 51` the same save is accepted and stored.

Test: `test_customer_form.py::test_a_bad_postal_code_is_shown_on_its_field`.

## Price form edge cases

**What it does.** On one deep-linked price step: 0 % tax gives tax 0.00; a tax
of 6.666 rounds half-up to 6.67; both amount limits are accepted and one cent
beyond each is refused with its named message; a commission at the configured
cap is accepted and above it refused with the cap message (cap read from the
demo's settings, not hard-coded).

Test: `test_price_form.py::test_price_form_edge_cases`.

## Price form detectors with positive controls

**What it does.** Two expected-red detectors built on
`verification.defect_detector.still_reproduces`, each with a positive control that
types an in-range amount and proves it round-trips through the calculation before
any verdict counts, and each run with its demo fault switch on and off:

| Detector | Expectation | Fault on | Fault off |
|---|---|---|---|
| negative net amount must be refused | closed (`closed=True`) | `EXPECTED-RED REGRESSED ON A CLOSED DEFECT` (asserted) | `closed_pass` |
| tax rounds half-up | open (`closed=False`) | `reproduces` (green) | `KNOWN DEFECT CLEARED` (asserted) |

The fault switches are `negative_price_accepted` and `rounding_drift`; the oracle
messages carry `demo_control.evidence()`, and each test asserts the fault set it
ran with (also visible on the screen's fault banner, `DemoPage.active_faults()`).
Signatures are keyed to the oracle's wording, not to the defect id.

```python
still_reproduces(
    "BUG-202",
    r"tax \S+ is not the half-up rounding \S+ of net x rate",
    oracle,
    exercised=control,
    closed=False,
)
```

Tests: `test_price_form.py::test_negative_price_detector_closed[healthy|negative-price-accepted]`,
`test_price_form.py::test_rounding_drift_detector[rounding-drift|healthy]`.

## Team plan: provisioned prerequisites to confirmed

**What it does.** A new team plan, a partner and a customer are created over the
API and read back for their reference numbers; the wizard selects them (plan by
its label, quarterly frequency) through to an active subscription whose plan,
parties, price and four quarterly transactions are checked over the API.

Test: `test_wizard_flows.py::test_team_plan_create_to_confirmed_via_ui`.

## Renewal step: back/forward, index chain, new term end

**What it does.** On a yearly subscription in its renewal window with every
period booked, *Renew* opens the indexation step.

* **Back/forward**: after an index is entered and the price step reached, browser
  back shows the indexation step with the same values and no error, forward
  returns to the price step, and the renewal confirms.
* **Index chain**: the new price must equal base x (1 + index); a value equal to
  base x index is reported as the bare-multiplier defect by name. After
  confirming, the yearly renewal transaction equals twelve times the new price, on
  the grid and in the API.
* **New term end**: exactly one calendar year after the old one (only 29 February
  may move, to the 28th; the oracle `one_calendar_year_later` has an offline test),
  both dates read-only, kept across back/forward, and stored on confirm.

Tests: `test_renewal_step.py::test_renewal_step_survives_back_forward`,
`::test_indexation_chain_on_screen_and_ledger`, `::test_term_end_extends_one_calendar_year`,
`::test_one_calendar_year_oracle` (offline).

## Task dates in the local zone

**What it does.** A hand-written table of UTC instants with their local date and
`HH:mm` in `Europe/Berlin` (midnight crossings, both daylight-saving switches,
year end, same-day rows). The **offline** test checks every row against the tz
database, that local time differs from UTC, and that both crossing and same-day
rows exist. The **browser** test sets the demo's `ui_timezone`, seeds one task per
row, proves the stored instant equals the one sent (control), and reads the list's
date and time for that row.

| Setting | Value |
|---|---|
| demo `ui_timezone` | `Europe/Berlin` (set by the test, restored by `demo_control`) |

Tests: `test_task_dates.py::test_row_table_matches_tz_database` (offline),
`test_task_dates.py::test_task_target_rendered_in_local_zone[...]`.

## Settlements party filter

**What it does.** Typing a fragment into the party filter narrows the options
locally and sends **no** list request (control: the input holds the text); choosing
an option sends exactly one request filtered on that party for the open tab, and
switching to the closed tab sends one more for the same party. Requests are
counted with a page request listener (`SettlementsScreen.list_requests`).

Test: `test_settlements_filter.py::test_party_filter_queries_only_on_selection`.
