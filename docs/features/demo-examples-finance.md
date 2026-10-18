# Demo examples: finance

Worked examples of finance checks against the demo system under test
(`demo_sut`, see [demo-sut.md](demo-sut.md)): invoices, the journal and its
ledger codes, ledger accounts, settlements and their statement export,
reseller terms, booking preconditions and promotion-check style API checks.
Everything is generic and invented; the reusable parts live in the framework
(`api/clients/ledger_client.py`, `oracles/invoice_integrity.py`,
`oracles/invoice_dates.py`, `oracles/ledger_journal.py`), the examples in
`tests/demo_examples/finance/`.

All examples are API level and run offline in `pytest tests/ -n auto`. The
detector examples write their verdicts to a per-test file
(`tests/demo_examples/finance/conftest.py`), because they produce a deliberate
`cleared` verdict; the fault set active during a test is recorded by
`demo_control` as the `demo_faults` user property and is on every demo response
(`X-Demo-Faults`).

## Billing client

**What it does.** `api.clients.ledger_client.LedgerClient` wraps `HttpEngine`
for a billing API: a subscription's invoices, one invoice, its journal entries
(`invoice_with_journal` picks the n-th invoice by number and returns it with its
journal), the journal filtered by references, transactions, booking due
invoices, ledger codes, accounts, renaming an account, settlements and the
statement export. Every request and its answer is attached to the Allure report.
Calls whose refusal is the behaviour under test (`rename_account`, `statement`)
return the raw `ApiResponse`.

**How to use it.** `LedgerClient(engine)`; in the examples the `billing`
fixture builds it on `demo_http`.

**Configuration.** `attach=False` turns the Allure attachments off (default on,
body cut at 4000 characters). `LedgerPaths` renames endpoints; defaults:
`subscriptions/{id}/invoices`, `subscriptions/{id}/invoices/process`,
`subscriptions/{id}/transactions`, `invoices/{id}`, `invoices/{id}/journal`,
`journal`, `ledger-codes`, `accounts`, `accounts/{id}`, `settlements`,
`statements`.

**Example.**

```python
billing = LedgerClient(HttpEngine("https://billing.example.test/api/"))
invoice, journal = billing.invoice_with_journal(subscription_id)
```

## Invoice and journal integrity

**What it does.** `oracles.invoice_integrity.check_invoice(invoice, journal)`
returns every broken rule: numeric invoice number, expected status (`OPEN`),
document id present, remaining equals total, total equals net plus tax, tax
rate within tolerance, at least one journal entry and every entry balanced
(`journal_balances`), plus the date rules below. The example
(`test_invoice_integrity.py`) books a seeded subscription's due periods and
checks every invoice, and shows the comparison is real by checking a wrong rate
is reported.

**How to use it.** `assert check_invoice(invoice, journal, tax_rate=20) == []`.

**Configuration.** Keyword arguments: `tax_rate` (default: the invoice's own
`tax_rate`), `expected_status` (`OPEN`), `tolerance` (0.01), `rate_tolerance`
(0.1 percentage points), `dates` (True), `payment_term_days` (30), `fields`
(`InvoiceFields` for other field names).

**Example.**

```python
problems = check_invoice(invoice, journal, tax_rate=20, dates=False)
```

## Invoice date rules

**What it does.** `oracles.invoice_dates`: the reference date is the latest of
booking date, document date and period start (`expected_reference_date`); the
due date is the document date plus the payment term (`expected_due_date`); the
collection date defaults to the reference date; the invoice period equals the
transaction's. `invoice_date_problems` lists every broken rule,
`assert_invoice_dates` raises with all of them. Eight invented use cases
(backdated, future-dated, mid-period, re-issued, year boundary) drive
`tests/oracles/test_invoice_dates.py`; the example
(`test_invoice_dates.py`) checks live invoices of a subscription that started a
month before the frozen clock, with the payment term set to 14 days.

**How to use it.** `assert_invoice_dates(invoice, transaction=tx, payment_term_days=14)`.

**Configuration.** `payment_term_days` (default 30), `collection_hint`
(default: none, so the reference date), `fields` (`DateFields`).

**Example.**

```python
expected_reference_date("2026-03-10", "2026-03-10", "2026-05-01")  # date(2026, 5, 1)
```

## Journal reader with a measured-absence guard

**What it does.** `oracles.ledger_journal` answers "did this transaction book
anything on account X?" without mistaking silence for proof:
`account_for_mapping` resolves the account mapped to a role from the service's
own mapping (an unknown role raises); `references_of` collects a transaction's
reference ids; `entries_referencing` and `lines_for_references` select the
journal entries that reference them (on the entry or on a line) and their
non-zero lines on the account; `claim_absent_on_account` asserts there are none
**only after** showing the account carries lines elsewhere in the journal that
was read, and raises `NotMeasured` otherwise.

The reader is not tied to one example. `test_closure_journal.py` shows the plainer
journal check next to it: a closure's journal entry balances (debits equal credits), and
the closure notice prints a `Credit:` equal to that entry and to the booked CANCEL total.

**How to use it.** Read the mapping and the journal from the service; never
hard-code the account number.

**Configuration.** `JournalShape` names the fields (defaults: entry references
`references` / `reference`, line references `references`, `lines`, `account`,
`debit`, `credit`).

**Example.**

```python
fees = ledger_journal.account_for_mapping(billing.ledger_codes(), "FEES")
ledger_journal.claim_absent_on_account(journal, ledger_journal.references_of(tx), fees)
```

## Fee block read as an observed shape

**What it does.** `tests/demo_examples/finance/_fees.py::FeeShape` reads a
subscription's `fees.net`, `fees.tax` and `fees.invoiced` through
`ObservedShape`: an absent value stays `None` and is never read as zero.
`all_present()`, `all_zero()` and `charges_fee()`; the last two raise
`ShapeNotObserved` when what they need was not observed. The example covers a
plan with a fee (before and after booking), a plan without one, and three absent
shapes (no block, empty block, net dropped).

**How to use it.** `FeeShape.of(subscription_json).charges_fee()`.

**Configuration.** None.

**Example.**

```python
fees = FeeShape.of(view)
assert fees.all_present(), fees.describe()
```

## Booking precondition over the API

**What it does.** `test_data/fixtures/demo/process_invoices.json` is a request
fixture replayed with `test_data.fixture_replay.FixtureSeeder`: it books a
subscription's scheduled transactions whose period starts by a date. The helper
`_support.process_due(engine, subscription_id, until)` is the booking
precondition the closure, settlement and journal examples reuse. The
example (`test_process_due_invoices.py`) asserts the due periods moved to
`BOOKED` with one invoice each, later periods stayed `SCHEDULED`, and a second
replay books nothing.

**How to use it.** `process_due(demo_http, sub["id"], "2026-04-15")`.

**Configuration.** Placeholders `${subscription_id}` and `${until}`.

**Example.**

```python
FixtureSeeder(engine).replay(Fixture.load(PROCESS_FIXTURE), {"subscription_id": 7, "until": "2026-04-15"})
```

## Ledger grid matches the captured schedule

**What it does.** `test_invoice_grid_matches_api.py` captures the confirm call's
response (it carries the scheduled transactions) and compares the detail
screen's ledger grid with it row by row; the schedule must satisfy net + tax +
fee net + fee tax = total, and the grid rows must be contiguous, one month long,
all `SCHEDULED`, all `NEW`, with the setup fee on the first period only
(`oracles.ledger_checks`). When the response carries no schedule the example
records an abstention (`EvidenceVerdict.no_verdict`) instead of passing.

**How to use it.** `pytest tests/demo_examples/finance/test_invoice_grid_matches_api.py`.

**Configuration.** None. The grid is server-rendered, so it is read over HTTP
(`_support.ledger_grid_rows` parses `ledger-grid` / `ledger-row` /
`td[data-col]`); no browser is needed.

**Example.**

```python
grid = ledger_grid_rows(demo_sut.client.get(f"/ui/subscriptions/{sid}").body.decode())
```

## Seeded subscription's ledger grid equals the API ledger

**What it does.** `test_ledger_grid.py` seeds a subscription, books its first
period only, opens the detail screen's `#ledger` deep link and requires the grid
to equal the API ledger: row count, periods, amounts, states (and each row's
`data-state` matches its state cell).

**How to use it.** `pytest tests/demo_examples/finance/test_ledger_grid.py`.

**Configuration.** None.

**Example.** `grid_row_of(transaction)` is how the grid must render one API row.

## Promotion checks: managed flag and statement export

**What it does.** `test_finance_api_delta.py` checks two finance API behaviours
the way a promotion check suite would:

* every ledger account carries a boolean `managed`, every account mapped to a
  role is managed, and at least one is not;
* the statement export of a settlement holds exactly one entry whose amount is
  the absolute closing balance, `CREDIT` when the balance is positive and
  `DEBIT` when negative, with the settlement reference and the partner as party.
  Both signs are exercised: a settlement over booked periods (positive) and one
  over only a closure credit (negative). An unknown settlement is refused
  with a 404 that names the id.

**How to use it.** `pytest tests/demo_examples/finance/test_finance_api_delta.py`.

**Configuration.** None.

**Example.**

```python
entry = billing.statement(settlement["id"]).json()["entries"][0]
assert entry["side"] == ("CREDIT" if balance > 0 else "DEBIT")
```

## Ledger-account rename rules

**What it does.** `test_accounts_rename.py`: every account the mapping uses reads
`managed: true`; renaming one answers 409 with the service's message and changes
nothing; an unmanaged account is renamed and then restored.

**How to use it.** `pytest tests/demo_examples/finance/test_accounts_rename.py`.

**Configuration.** None.

**Example.**

```python
assert billing.rename_account(account_id, "New name").status == 409
```

## Reseller share kept (detector pair)

**What it does.** `test_reseller_terms.py::test_share_percent_kept` creates reseller terms
from a fixture (`fixtures/reseller-terms-six.json`, share `6`, or
`reseller-terms-two-and-a-half.json`, share `2.5`; both with a flat fee of `1.25`), reads them
back, puts a reseller on them, books a subscription and settles the reseller. The oracle
requires the stored share to be the one sent (`oracles.assert_share_kept`) and the settled
amount to equal `expected_commission(period net, share, flat fee)`. The control requires the
flat fee to read back as sent and something to have been settled. With the demo fault
`reseller_share_scaled_twice` on, the pair reads `reproduces` for `DEMO-114`; with it off,
`cleared`.

**How to use it.** `pytest tests/demo_examples/finance/test_reseller_terms.py`.

**Configuration.** None. The terms name carries a tag (`${tag}` in the fixture, bound with
`test_data.fixture_replay.bind`).

**Example.**

```python
oracles.assert_share_kept(stored_terms, 2.5)
```

## Share-scale instrument self-test

**What it does.** `oracles.read_share(terms)` keeps three states apart: a number reads as a
`Decimal`, a string or boolean reads as `None`, and an absent field reads as `MISSING`.
`test_reseller_oracles.py` pins them, the oracle's signature (only for a rescaled share,
never for another wrong value or an unreadable one), the detector wrapping, that both
fixtures send numeric shares and the same flat fee, and the commission arithmetic
(97.50 at 6 % plus 1.25 is 7.10).

**How to use it.** Runs with the offline suite.

**Example.**

```python
read_share({"share_percent": "6"})  # None: a string is not a share
```

## Replayed payment counted once (detector pair)

**What it does.** `test_payment_idempotency.py::test_replayed_payment_counted_once` pays part
of an invoice twice with the same `Idempotency-Key`. The oracle requires the replay to return
the first payment and the invoice's remaining amount to drop only once. The control pays
twice with two different keys and requires two payments. With the demo fault
`payment_replay_double_counted` on, the pair reads `reproduces` for `DEMO-107`.

## Billing screens coverage analysis

**What it does.** `specs/billing-ui-coverage.md` analyses the demo's billing
screens: what each renders and which `data-testid` hooks it offers, which
behaviours the examples already verify (and at which level), a prioritised
backlog of what is left (with the locators each item needs), and two scenarios
in the spec format.

**How to use it.** Review it with the specs; generate a browser test from a
scenario with `TestGenerator.generate_from_spec("specs/billing-ui-coverage.md")`.

**Configuration.** None.

**Example.** Backlog item 1: *Edit* is disabled exactly for managed accounts.

