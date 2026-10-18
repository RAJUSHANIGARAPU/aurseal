# Demo examples: subscription lifecycle

Worked examples of the create → bill → renew lifecycle against the in-repo demo
service (`demo_sut`, see [demo-sut.md](demo-sut.md)). They live in
`tests/demo_examples/lifecycle/` and run offline with the rest of the suite:

```bash
pytest tests/demo_examples/lifecycle -n auto
```

All of them are API-level. They seed through the project's own layers, never through
the demo's internals:

* `FixtureSeeder` replays the JSON request fixtures in `tests/demo_examples/fixtures/`;
* `LiveResolver` resolves live partner and plan ids by name or code;
* `PrerequisiteFactory` provisions what a seed depends on, with after-hooks;
* `select_targets` / `replenish` / `CleanupLedger` / `open_draft` cover seed modes,
  pool stocking and cleanup.

Every example freezes the demo clock at `2026-03-10T09:00:00Z`
(`tests/demo_examples/lifecycle/support.py::NOW`). The seeder's run day is the same
date, so `${RUN_DAY_DATE_MINUS_365D}` in a fixture and "today" on the demo agree.

## Lane runner

**What it does.** `scripts/run_lane.py` runs one lane (a marker expression and/or a set of
paths) as a single pytest invocation with a JUnit XML report. It then prints one
`PASS` / `FAIL` / `SKIP` line per test class, and a totals line. The table is read from the
report, never from console output, so it shows exactly what CI would publish. A class with
any failure or error is `FAIL`. A class with only skips is `SKIP`.

**How to use it.**

```bash
python scripts/run_lane.py tests/demo_examples/lifecycle --name lifecycle
python scripts/run_lane.py -m "seeded and not slow" -- -n auto   # after -- : passed to pytest
```

The exit code is pytest's own (5 when the lane selected nothing). It is 3 when pytest left
no readable report. A report from an earlier run is deleted first, so it is never
summarised by mistake.

**Configuration.** `LANE_REPORT_DIR` (default `reports/lanes`): the directory for
`<lane-name>.xml`. Use `--name` to set the lane name. Without it, the name comes from the
marker expression or the first path.

```
PASS  tests.demo_examples.lifecycle.test_renewal_due        3 passed, 0 failed, 0 skipped
FAIL  tests.demo_examples.lifecycle.test_renewal_schedule   0 passed, 1 failed, 0 skipped
FAIL  total                                                 3 passed, 1 failed, 0 skipped
```

## Renewal indexation oracle

**What it does.** Derives every renewal figure from the pre-index base amount and an index
factor `f`. The functions live in `oracles/charges.py`:

* `indexed_amount(base, f)` is `base x (1 + f)`, rounded half-up to cents.
* `bare_multiplier_misread(base, f)` and `truncated_percent_misread(base, f)` are the named
  wrong readings: `base x f`, and the index percentage cut to a whole number.
* `indexed_renewal(schedule, base, f, term_months)` runs the indexed amount through a
  `ChargeSchedule`: surcharge by term, then net, tax and total.
* `explain_indexed(observed, base, f)` names which reading an observed price matches:
  `indexed`, `bare_multiplier_misread`, `percent_truncated_misread`, `not_indexed` or
  `unexplained`.

`verification/_temporal.py::add_years_clamped(day, years)` extends a term by whole calendar
years. A 29 February date becomes 28 February in a non-leap year.

**How to use it.** Compare what the system booked with the oracle, and report the reading
by name when they differ:

```python
from decimal import Decimal as D
from oracles.charges import ChargeSchedule, explain_indexed, indexed_renewal

schedule = ChargeSchedule({1: D("0.03"), 12: D(0)}, tax_rate=D("0.20"))
expected = indexed_renewal(schedule, base=D("40.00"), index_factor=D("0.025"), term_months=12)
assert (renewal.net, renewal.tax) == (expected.net, expected.tax), explain_indexed(
    observed_monthly, D("40.00"), D("0.025")
)
```

**Configuration.** None. Rates are passed in as data.

**Demo example.** `test_renewal_indexation.py`:

* It sets an invented index (`index_percent=3.0`) and runs the demo's renewal job. It then
  checks the renewed price, the one-year term extension and the booked renewal amounts
  against the oracle.
* A 29 February term end renews to 28 February.
* `test_misread_is_named_when_the_percent_is_truncated[fault-on/off]` (`DEMO-106`): a
  2.5 % index must read as `indexed`. With the demo fault `index_percent_truncated` on,
  `explain_indexed` names the truncated reading. The control indexes by a whole 3 %, which
  cannot be truncated.

## Renewal schedule checks

**What it does.** Two checks in `oracles/ledger_checks.py`, next to `contiguous` and
`additive_identity`. Each returns a list of problems:

* `renewal_row_starts_on(rows, previous_term_end)`: exactly one renewal row (`kind="RENEW"`
  by default), starting on the previous term's end date;
* `collects_indexed_amount(row, indexed, pre_index)`: the renewal row collects the indexed
  amount. When it collects the pre-index amount instead, the problem says so.

**How to use it.** Run every check and assert the combined list is empty, so one run
reports everything:

```python
rows = support.as_rows(transactions)   # dates parsed, inclusive end dates
problems = [
    *lc.renewal_row_starts_on(rows, old_term_end),
    *lc.contiguous(rows),
    *lc.additive_identity(rows, ["net", "tax", "fee_net", "fee_tax"], "total"),
    *lc.collects_indexed_amount(renew_row, indexed_net, pre_index_net),
]
assert problems == []
```

**Configuration.** Use keyword arguments for other shapes: `kind`, `renewal_kind`, `start`,
`field`, `tolerance` (default 0.01).

**Demo example.** `test_renewal_schedule.py` renews a backdated yearly subscription and
runs all four checks over the demo's transactions. The healthy/defective unit pairs are in
`tests/oracles/test_renewal_math.py`.

## Seed fixtures and fixtures for the lifecycle

**What it does.** Each fixture is an invented request journey in
`tests/demo_examples/fixtures/`. `tests/demo_examples/lifecycle/conftest.py` replays them
with live ids:

| Fixture file | pytest fixture | Ends in |
|---|---|---|
| `customer-standard.json` | `verified_customer` (factory prerequisite with an after-hook that verifies the customer) | a verified customer |
| `subscription-to-pending-payment.json` | `seeded_pending_subscription` | `pending_payment`: create, set customer, plan and term, payment method, quote, submit the quote |
| `subscription-to-active.json` | `seed_active_subscription()` | `active` |
| — | `seed_billed_subscription()` | `active` with the first invoice billed |
| `subscription-backdated-renewable.json` | `seed_renewable_subscription(frequency=)` | started `${RUN_DAY_DATE_MINUS_365D}`, every period before the renewal date billed, so a renewal is due today |

The partner and plan are resolved by name and code with `LiveResolver`. It refuses a name
that matches more than one record.

**How to use it.** Request the fixture in a test under `tests/demo_examples/lifecycle/`:

```python
def test_something(demo_http, seed_renewable_subscription):
    seeded = seed_renewable_subscription(frequency="MONTHLY")
    draft = support.open_renewal(demo_http, seeded["subscription_id"])
```

`unbound_placeholders(fixture, params)` checks a fixture offline, so a renamed parameter
fails before anything is sent (`test_seed_to_pending_payment.py`).

**Configuration.** No environment variables. The run day is `support.TODAY`. Account numbers
come from `support.new_account_number()` (invented, checksum-valid).

**Demo examples.**

* `test_seed_to_pending_payment.py`: every step is bound and read back.
* `test_create_to_state.py`: active and billed seeds. The fixtures stock the reuse pool with
  `replenish`. `select_targets` then picks targets under `fresh`, `reuse` and `targets`
  (seed modes, see [test-data.md](test-data.md)). `targets` mode never builds a missing
  shape.
* `test_renewal_due.py`: the backdated seed is billed up to the renewal date. Its yearly and
  monthly variants are tested separately. A stale open draft is discarded and the renewal
  retried with `open_draft`. The new draft is recorded in the `cleanup` ledger and discarded
  at teardown.
* `test_customer_verification_gate.py`: an unverified customer is refused with
  *Customer is not verified.* The prerequisite's after-hook opens the gate.

## Creation: parties step

**What it does.** Picks the reseller and customer by their issued number, not their display
name. Names are not unique, and `select_unique` refuses an ambiguous name. After the
create, it checks that each party field is bound to the record that was picked, then sets
the payment frequency explicitly before submit. A create refused with 429 is sent again
(`support.retry_create`, honouring `Retry-After`) up to a bound, because a throttle provably
stored nothing; any other refusal (409, 422, 504) is never resent. A search afterwards finds
exactly one subscription per intended create.

**How to use it.**

```python
response, attempts = support.retry_create(
    lambda: demo_http.post("subscriptions", json=body), attempts=3
)
```

**Configuration.** `attempts` (default 3) and `sleep` (the wait between sends, injectable for tests).

**Demo example.** `test_create_parties_step.py` runs with the demo fault
`create_throttled_every_other` on and off; with it on, two intended creates always meet one
429, so the resend is exercised. `test_only_a_throttle_is_resent_and_never_more_than_allowed`
pins the rule offline.

## Creation: customer screening step

**What it does.** Finds the screening button on the new-subscription screen by any of its
known labels (`screens.match_label_variant`), so a renamed button does not break the flow,
and logs which label matched. It then completes the screening over the API. An unknown
label fails and lists the accepted ones.

**How to use it.** `element_texts(html, testid)` reads the text of every element with that
`data-testid` from a server-rendered page, without a browser.

**Configuration.** The demo setting `screening_label` switches the label. The example runs
once per label.

**Demo example.** `test_verification_step.py`.

## Creation: immediate activation path

**What it does.** Runs the second, shorter creation path as one case of the creation
example. With `activate_immediately` the subscription is active straight away: no quote
summary and no `pending_payment` stop. The standard path goes draft → pending_payment →
active. Both end with a schedule and an account-opened letter.

**Demo example.** `test_instant_activation_path.py` (parametrised).

## Renewal end to end

**What it does.** Renews a fresh subscription and a reused one. The reused one is picked by
shape from a `ReusePool`: active, term ending within the window, every earlier period
booked. The example runs only the steps the renewal draft currently shows
(`support.run_visible_steps`). It submits the prefilled price step without retyping the
start date, then confirms. Finally it bills the new term and checks three things: the first
new invoice is a renewal, its document and due dates are right, and exactly one renewal
notice exists for the new version.

**Configuration.** `SEED_MODE` semantics come from `test_data.seed_mode`. The example sets
the mode per case.

**Demo example.** `test_renewal_to_active.py`.

## Renewal indexation step: readers and back-navigation guard

**What it does.** `renewal_step.RenewalStep.read(draft)` reads the step's values: the index
percentage, the old and new term end, and the current and new price. A value the draft does
not carry stays `None`; it is never filled with a guessed default.

`read_only_changed(before, after)` checks that the date fields stayed read-only.
`lost_after_round_trip(before, after)` names every required value that was present before
Back and missing after Next. That is the shape of a step left stuck on a required-field
error.

**How to use it.**

```python
first = RenewalStep.read(draft)
...  # Next with another index, Back to the first
assert lost_after_round_trip(first, RenewalStep.read(draft_again)) == []
```

**Demo example.** `test_renewal_back_navigation.py` sends tampered dates and gets them
ignored. It then changes the index and changes it back, and every value survives. The
browser version of the back/forward check is `ui/test_renewal_step.py`.

## Customer creation probe matrix

**What it does.** Drives one parametrised fixture, `customer-matrix.json`, as a probe
matrix. Its placeholders are `${DISPLAY_NAME}`, `${EMAIL}`, `${ACCOUNT}`, `${COUNTRY}`,
`${POSTAL_CODE}` and `${SHARE}`. The journey is: create the customer (`create`), verify
them (`verify`), and subscribe them with that reseller share (`subscribe`).

`probes.run_probe` replays the fixture step by step. Unlike `FixtureSeeder`, a refusal is
the observation, not an error. The probe records which step refused it, the status and
the message, and turns each outcome into `RejectionSignals`. "Accepted" therefore means no
surface signalled a refusal. Each probe asserts its expected outcome:

| Probe | Changes | Refused at | Message |
|---|---|---|---|
| `baseline` | nothing | - | - |
| `name-at-limit` | a 64-character name | - | reads back identical |
| `name-over-limit` | 65 characters | `create` | Display name may be at most 64 characters. |
| `non-ascii-name` | `Zoë Ångström` plus the tag | - | - |
| `email-missing-for-email-delivery` | no e-mail | `subscribe` | Invoices by e-mail need an e-mail address. |
| `account-checksum` | check digits off by one | `create` | Account number fails its check digits. |
| `foreign-postal-code` | country `AA`, postal code `12345` | - | - |
| `share-at-cap` | share 20 (the cap) | - | - |
| `share-over-cap` | share 21 | `subscribe` | Reseller share may not exceed the cap. |

**How to use it.**

```python
result = run_probe(demo_http, load_fixture("customer-matrix"), {**base(tag), "SHARE": 21},
                   today=support.TODAY)
assert result.refused_at == "subscribe", result.describe()
```

Before anything is sent, the fixture is checked offline with `fixture_contract` (the
fields the service requires are present) and `unbound_placeholders`.

**Configuration.** Account numbers are generated with the public mod-97 check
(`demo_sut.customers.make_iban`). They are never real.

**Demo example.** `test_customer_creation_probes.py`.

## Customer rules at creation

**What it does.** Three API examples in `test_customer_validation.py`:

* `test_payout_account_check_digits_at_creation`: an account number whose check digits are
  off by one is refused with 422, the named message *Account number fails its check
  digits.* and the field `payout_account`. A valid one, sent with spaces, is accepted and
  reads back in compact form.
* `test_email_delivery_needs_an_email_on_the_customer`: a customer without an e-mail cannot
  have invoices delivered by e-mail (422 on `invoice_delivery`), but can with delivery to the
  portal.
* `test_long_display_name_kept_detector[fault-on/off]` (`DEMO-112`): names of 41, 52 and 64
  characters read back whole and 65 is refused. With the demo fault
  `customer_name_truncated` on, names over 40 characters are cut without an error and the
  detector reports `reproduces`. The control is a 40-character name, which reads back
  either way.

`test_customer_read_after_write.py::test_customer_read_after_write_detector[fault-on/off]`
(`DEMO-111`, fault `customer_read_cached_after_update`) reads a customer, updates the
e-mail and reads it again; the control shows the uncached list already carries the new
value.

**Configuration.** None. The active fault set is recorded as `demo_faults` in each test's
evidence.

## Leap-day start (detector)

**What it does.** The control creates a subscription starting on 28 February 2028. The
oracle starts one on 29 February 2028 and requires it to be created with a term ending on
28 February 2029. With the demo fault `date_parser_rejects_leap_day` on, the detector
reports `reproduces` (`DEMO-105`). With it off, both pass.

**Demo example.** `test_date_formats.py::test_leap_day_start_detector`
(parametrised `fault-on` / `fault-off`).

## Future-start subscription

**What it does.** Reads a subscription that starts in a later year and checks three things:
it is confirmed (`active`), its number is well formed (`SUB-` + six digits), and its first
scheduled period falls in its start year, not the current year.

**Configuration.** `DEMO_FUTURE_START_SUBSCRIPTION_ID`: use an existing subscription instead
of seeding one. The test skips when that id does not exist or does not start in a later
year.

**Demo example.** `test_future_start.py`.

## Prerequisite chain

**What it does.** `PrerequisiteFactory` provisions reseller terms, a reseller bound to them,
a plan and a customer over REST, in dependency order. Every name carries the run's
`${SEED_TAG}`, and dependants receive parent ids as `${reseller_terms.id}`. The example
checks four things: every id is set, the reseller number is not blank, the plan code carries
the tag, and the chain works end to end. A subscription for the new reseller takes the
terms' share.

**How to use it.**

```python
factory = PrerequisiteFactory(replay, [
    Prerequisite("reseller", load_fixture("reseller"), depends_on=("reseller_terms",)),
    Prerequisite("reseller_terms", load_fixture("reseller-terms"),
                 params={"share_percent": 4.5}),
])
built = factory.provision("reseller")
```

**Demo example.** `test_prerequisites.py`. The fixtures are `reseller-terms.json`,
`reseller.json`, `plan.json` and `customer-standard.json`.

## Renewal outcomes

**What it does.** Four examples in `test_renewal.py`:

* `test_renewal_books_renewal_coded_transaction`: the renewal books one `RENEW`
  transaction covering exactly the next term.
* `test_backdated_subscription_renews`: uses the demo clock instead of a run-day offset. It
  starts a subscription "a year ago", moves the clock into the renewal window and renews to
  `renewed`.
* `test_indexation_applies_to_net_price`: baselines the monthly net, tax and gross and the
  period net and tax, then renews over REST with a 4 % index. Every baselined amount must be
  recomputed from the indexed net price. Tax is recomputed with the oracle, not scaled.
* `test_reused_subscription_renews_to_confirmed`: takes up to two fully billed, renewable
  subscriptions from the reuse pool (it must not be empty) and renews each one.

The demo's status after a renewal is `renewed`. It plays the role of "confirmed" here.
