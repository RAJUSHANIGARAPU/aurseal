# Demo examples: mid-term changes

Worked examples that change active subscriptions of the demo service (`demo_sut`) through
change drafts and judge what was persisted, priced and booked. They live in
`tests/demo_examples/change/`, run offline (localhost only, no browser) and are part of
`pytest tests/ -n auto`.

```bash
pytest tests/demo_examples/change -n auto
```

**Configuration.** No environment variable is needed. The examples use the session's demo
instance (one per xdist worker, see [demo-sut.md](demo-sut.md)); `DEMO_SUT_URL` points them at
a running instance instead. Each example freezes the demo clock and seeds its own data;
faults and settings are reset at teardown and the faults seen are recorded as the
`demo_faults` user property. The area `conftest.py` sends detector and coverage verdicts to a
per-test file, so the invented defects never reach the run's real verdict evidence.

Shared steps (`support.py`): `active_subscription` (create, confirm, book up to "now"),
`open_change` / `finish_change` / `change` (open a change draft, accept the ownership step when
the draft has one, confirm), `current`, `change_transactions`, `read_path`
(`"invoice_notes.-1.text"`) and `reuse_targets` (the reuse pool in `reuse` mode).

## Change-case manifest

**What it does.** `change-cases.json` is an invented list of eighteen subscription changes,
derived from what `demo_sut` accepts in a change draft. It is grouped by the rule that decides
each change, and the id says the group: `CHG-TERMS-n` (price, spend cap, seats),
`CHG-BILL-n` (payment, invoice delivery, billing contact, PO reference, invoice notes) and
`CHG-SETUP-n` (add-ons, data region, workspace type, labels) are edits the change accepts;
`CHG-OWNER-1` is accepted only once the new owner agrees; `CHG-LOCK-n` touch a field fixed for
the term (`currency`, `frequency`, `plan_id`); `CHG-LIFE-1` needs a lifecycle state nothing
leads into. Every case has an honest state: `automated` (names its `test`), `planned` (says
what it is `waiting_on`) or `manual` (gives `why_manual`). Fifteen are automated, the two plan
moves are planned (`plan_id` is refused in a change) and the pause is manual (no transition
enters `suspended`). It loads through `test_data.scenario_manifest.CaseManifest`. A case counts
as proven only when it is automated, `seen_passing` and carries a `proof`.

`test_change_case_manifest.py` keeps it honest: unique ids; cases that can be driven name
`action`, `ends_in`, `reasons` and `touches`; every action is an action of
`demo_sut/config/subscription.machine.yaml` and can end in the case's state; planned and
manual cases say why; automated cases point at a test function that exists; generic cases
carry `apply` and `then`; every case records its `origin`; ids follow the group scheme;
every field `demo_sut` locks for the term has a case; and the proven count is logged apart
from the automated count.

**How to add a case.** Give it an id, a title and a state. A case driven by the generic test
sets `test` to `...test_change_scenarios.py::test_change_case` and carries `apply` (the
draft's change body) and `then` (`read_path` path -> value). Set `seen_passing` and `proof`
only after you watched it pass.

## Generic change cases (manifest-driven)

**What it does.** `test_change_case` runs once per case that names it. It builds a
subscription with a known baseline, asserts every expected value differs from the baseline
(otherwise the check would be vacuous), applies the case's `apply` through a change draft
and asserts the subscription ends in the case's state, shows every expected value and
booked exactly one more CHANGE transaction.

```bash
pytest "tests/demo_examples/change/test_change_scenarios.py::test_change_case[CHG-SETUP-2]"
```

The cases that need more than "set a field, read it back" have their own tests in the same
module:

| Test | What it adds |
|---|---|
| `test_spend_cap_change_price_unchanged` | the CHANGE row is zero and the price did not move |
| `test_addons_flip_one_at_a_time` | each add-on flips in its own change; only that one moves |
| `test_data_region_move` | three moves in a row, and an unknown region is refused |
| `test_workspace_type_change` | the workspace type can be cleared again (it is optional) |
| `test_amend_invoice_note_across_two_changes` | the note is amended in place (same number, version 2); the earlier version keeps its text |
| `test_add_label` | labels read back with their code and title |
| `test_price_only_change_leaves_other_fields` | every baseline field is identical after a price-only change |
| `test_create_process_change_lifecycle` | 37.50 net becomes 45.00 gross at 20 % tax, one CHANGE row |
| `test_direct_debit_account_verification` | the first name check answers `NO_MATCH` and is recorded, the second `MATCH`; direct debit without an account is refused |
| `test_seat_blocks_change_persists` | the seat blocks and their total persist |
| `test_ownership_transfer_needs_acceptance` | confirming before the new owner accepts is refused |
| `test_billing_contact_verbatim_price_unchanged` | the contact is stored exactly as typed, spaces and apostrophe included |

**Configuration.** `test_direct_debit_account_verification` uses the setting
`account_check_mismatch_once` (accounts that answer `NO_MATCH` once); it is reset at teardown.

## Fields locked for the term

**What it does.** `test_locked_fields.py` covers the fields a change may not edit
(`currency`, `plan_id`, `frequency`). `test_currency_change_is_refused` (case CHG-LOCK-1) reads
the currency first, tries to change it and asserts a 422 naming the field, with the
subscription still active and no draft left open.
`test_every_locked_field_is_refused_on_open_and_on_autosave` tries each locked field both when
the draft is opened and on an autosave. `test_the_change_screen_shows_the_locked_fields_read_only`
reads the change screen's HTML: the frequency and currency inputs are `readonly`.

## Customer-side changes

**What it does.** `test_customer_change_variants.py` covers four variants in one parametrised
example on a company holder: a rename and a new e-mail (both visible on the subscription at
once, without a change, because the holder is read from the current customer record), an
ownership transfer (the draft gains the `ownership_acceptance` step and the new owner must
accept) and a second verification (the customer's `verified_at` moves forward). Each variant
first proves its reader works: the baseline value is read and not blank.

## Editing the customer over REST before a change (BillingClient)

**What it does.** `api.clients.billing_client.BillingClient` wraps the subscription billing
calls on top of `HttpEngine` (every call must answer 2xx, otherwise `ApiError` with the
response). `update_customer` PATCHes the customer record behind a subscription, so a
following change starts from a record that changed under an existing subscription.

```python
from api.clients.billing_client import BillingClient

billing = BillingClient(demo_http)  # HttpEngine on <base>/api/
customer = billing.customer_of(subscription_id)  # resolves the holder's record
billing.update_customer(customer["id"], type="COMPANY", display_name="Fernvale Co")
draft = billing.open_draft(subscription_id, "change", changes={"customer_id": successor_id})
billing.accept_ownership(draft["id"])
billing.confirm_draft(draft["id"])
```

Also: `subscription(id, version=)`, `transactions`, `customer`, `patch_draft`,
`discard_draft`. Example: `test_customer_change_then_subscription_change.py`.

## Future-dated change

**What it does.** A change effective in the future returns a draft id, writes a version whose
`version_status` is `scheduled`, schedules one non-zero CHANGE transaction starting on the
effective date, and leaves the current version (and price) untouched until the clock reaches
that date. Example: `test_future_change.py`.

## Baseline-gap classification (offline)

**What it does.** `tests/demo_examples/change/oracles.py` decides what a blank derived holder
field before a change means, so a change is never blamed for an old gap:
`blank_derived_fields(block)` lists blank fields (`None`, empty or whitespace strings, empty
containers), `classify_baseline_gap(blank, built_this_run=...)` answers `NONE`, `EXCLUDE`
(borrowed data: leave the blank fields out of the comparison), `ABSTAIN` (borrowed and every
field blank) or `FAIL` (a gap on data this run built), and `fields_to_compare` returns the
fields a before/after comparison may judge.

```python
blank = blank_derived_fields(before["holder"])  # e.g. ["phone"]
gap = classify_baseline_gap(blank, built_this_run=target.was_seeded)
for field in fields_to_compare(blank, gap):
    assert after["holder"][field] == before["holder"][field]
```

Unit tests: `test_change_oracles.py`.

## Notes keep their title (both plans)

**What it does.** `test_notes.py::test_note_keeps_title[TEAM/INDIVIDUAL]` opens a note draft,
autosaves the title and then the description, reads the draft back (title intact), submits it
(2xx) and reads the stored note; a note never changes the subscription's status or version.
`test_note_without_title_is_refused` shows a note without a title is refused with a field
error.

## Mid-period price raise books one pro-rata change

**What it does.** `test_proration.py::test_a_mid_period_raise_books_one_change_under_the_ceiling[TEAM/INDIVIDUAL]`
picks a date inside the last booked period (`choose_mid_period_date`) on a subscription with
an add-on and raises the price from that date. Exactly one CHANGE transaction is booked,
starting on that date; its amount has the sign of the raise (`sign_matches`) and stays under
the whole-period ceiling (`ChargeSchedule.period_delta_ceiling`, monthly, 20 % tax); no
existing transaction disappears. `test_period_start_change_books_full_delta` shows a change on
a period start books the whole delta.

`test_mid_period_change_is_prorated_detector[fault-on/off]` is the detector pair for
`DEMO-103` with the `proration_floors_to_zero` fault: the control is a period-start change
that books the full delta, the oracle is that a change ten days in books exactly the
remaining-days share, strictly between zero and the full delta. The pair is run by
`tests/demo_examples/detector_pair.py::judge` (see
[demo-examples-cancellation.md](demo-examples-cancellation.md#detectors-with-fault-switches)).

## Rejection attribution: environment wins (offline)

**What it does.** `rejection_text(status, body)` flattens a rejected response (status, error
code, message, field errors) into one line; `START_CHANGE_ENVIRONMENT` and
`START_CHANGE_DEFECT` are the patterns the start-date detector hands to
`verification.defect_detector.attribute_failure`. Unit cases in `test_change_oracles.py`: a 409
*A draft already exists* and a 404 *No API route* are environment; the validators for other
rules (already booked, not active) are environment; a rejection naming the start date is the
defect; environment wins over an echoed start date; 500, 404 not-found and blank answers are
unattributable.

```python
text = rejection_text(response.status, response.json())
attribute_failure(text, defect=START_CHANGE_DEFECT, environment=START_CHANGE_ENVIRONMENT)
```

## Start-date change keeps day and month (detector)

**What it does.** `test_start_date_change.py` moves the start of a confirmed, not yet booked
subscription. The capture is attributed first: an environment or unattributable rejection
means the change was not exercised, so environment noise never reads as "still reproduces";
only a rejection naming the start date reaches the oracle.

* `test_an_unbooked_start_date_moves_and_reschedules`: the new start and term end persist and
  the first live period starts on the new date.
* `test_start_date_change_on_a_booked_subscription_is_not_the_defect`: once a period is booked
  another validator refuses the move, and it is attributed to the environment.
* `test_start_date_change_keeps_day_and_month_detector[fault-on/off]` (`DEMO-104`, fault
  `effective_date_day_month_swapped`): moving the start to `2026-03-04` must schedule from
  that date. The control moves another subscription to the 15th, a day that cannot be read
  the other way round.
