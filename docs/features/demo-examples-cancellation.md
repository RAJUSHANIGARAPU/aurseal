# Demo examples: cancellation

Worked examples that cancel subscriptions of the demo service (`demo_sut`) and judge the
credit, the reason, the ledger legs and the closure notice with the project's oracles.
They live in `tests/demo_examples/cancellation/`, run offline (localhost only) and are part
of `pytest tests/ -n auto`.

```bash
pytest tests/demo_examples/cancellation -n auto
```

**Configuration.** None of the examples needs an environment variable. Each test starts from
the session's demo instance (one per xdist worker, see [demo-sut.md](demo-sut.md)); set
`DEMO_SUT_URL` to run them against an already-running demo instead. Every example freezes
the demo clock, so the dates below are fixed. Fault switches and settings changed by a test
are reset at teardown, and the faults seen are recorded as the `demo_faults` user property.

Shared steps (`support.py`): `processed_subscription` (create, confirm and book every period
up to "now"), `last_paid` (the newest booked period, read **before** acting), `cancel`
(through a cancel draft), `assert_refund_within_bounds` and `reuse_targets` (the reuse pool).

## Charge legs of a booked row

**What it does.** `oracles.ledger_checks.ChargeLegs` reads the money legs of one booked row
(price net/tax, fee net/tax, total) and keeps two questions apart: is the fee leg *present*,
and does the row *charge no fee*? An absent fee leg counts as zero, and
`unexplained_total` (total minus the price legs) still shows a fee that vanished from the
payload while its money stayed in the total. A row whose price legs or total cannot be read
is never judged fee-free.

**How to use.**

```python
from oracles.ledger_checks import ChargeLegs

legs = ChargeLegs.of(cancel_tx)  # field names configurable: price_net="premium", ...
legs.fee_leg_present  # False when neither fee field is in the payload
legs.unexplained_total  # total - price net - price tax, None if unreadable
legs.fee_legs_explain_total()  # fee legs account for the unexplained money
legs.charges_no_fee()  # no fee on the legs and none hidden in the total
legs.describe()  # one line for an assertion message
```

**Configuration.** `tolerance` (default one cent) on `charges_no_fee` and
`fee_legs_explain_total`.

**Example.** `test_cancellation_charge_legs.py::test_credit_legs_are_explained` reads a
real closure transaction: its legs are fee-free, booked negative and explained.
`test_legs_explain_the_total_to_the_cent[fault-on/off]` sets the tolerance to zero: with the
`refund_legs_rounded_independently` fault on, the legs miss the total by exactly one cent.

## The closure reason is kept

**What it does.** Opens a cancel draft without a reason (confirming it is refused with a
field error on `reason`), autosaves the reason `CONSOLIDATING`, reads the draft back,
confirms it and checks the stored version carries exactly that reason and the version
cause `cancel`.

**How to use.** `test_cancellation.py::test_the_closure_reason_is_kept`. The precondition
(nothing closed yet) is asserted, not assumed.

## Mid-period credit within bounds (both plans)

**What it does.** Builds a processed subscription, reads the paid period first, closes it
11 days after "now" (inside that period, chosen with `choose_mid_period_date`) and asserts
the credit with the convention-independent oracle `assert_pro_rata_refund`: negative,
strictly smaller than the full period, taxed at the plan's rate, and consistent with the
remaining days under at least one modelled convention. Later unpaid periods must be voided.

**How to use.** `test_a_mid_period_credit_stays_within_the_bounds[TEAM/INDIVIDUAL]`.

```python
assert_refund_within_bounds("SUB-004512 closed 2026-09-14", paid_tx, chosen, view)
```

## The exact credit under the configured convention

**What it does.** Reads the demo's `day_count` setting back (`actual/period` or `30/360`),
maps it to the oracle's convention and asserts credit net and tax equal `candidate_refund`
exactly, on three dates including the 30th of a month. A separate example closes a monthly
period in February 2028, a leap year: on the 15th the two settings give different credits,
and each must match its own convention. A test pinned to the wrong convention can pass for
months and go red only on a date like this one.

**How to use.** `test_the_exact_credit_under_the_configured_convention` (parametrised over
setting x dates) and `test_leap_february_closure_pins_the_convention`.

**Configuration.** The mapping is `DEMO_CONVENTIONS` in the test module:

| demo `day_count` | oracle convention |
|---|---|
| `actual/period` | `DayCount.ACT_ACT` |
| `30/360` | `DayCount.THIRTY_E_360` |

## Reuse-based closure and notice reconciliation

**What it does.** `reuse_targets` runs the project's reuse pool (`test_data.pool`) in
`reuse` mode: it borrows active subscriptions whose newest paid period contains the chosen
date (no open draft, only price transactions so far, an account to pay a credit to) and
builds a processed one when none fits. `test_reused_subscriptions_close_within_the_bounds`
closes up to 15 candidates; each must end `cancelled` with one CANCEL transaction starting on
the chosen date and a credit within bounds. When neither borrowing nor building works the
test skips with the reason. `test_a_reused_subscription_closes_and_its_notice_reconciles`
closes up to three and reconciles the closure notice (PDF text, `en_GB` amounts) with the
CANCEL transaction: `Credit`, `Credit net` and `Credit tax` equal the booked legs and are
negative, and `Ends on` matches the chosen date.

**Configuration.** Reading the PDF needs `pypdf` (already a project dependency); without it
the reconciliation example skips.

## The credit against what was paid

**What it does.** `test_credit_equals_unearned_share` closes 12 days into a paid period and
asserts the credit total equals the unearned share of net plus tax under `actual/period`,
and never more than what was paid for that period.

## Detectors with fault switches

Each detector runs `verification.defect_detector.still_reproduces` with a positive control, as
a pair: fault on, the verdict must be `reproduces`; fault off, the detector must report
`KNOWN DEFECT CLEARED`. `tests/demo_examples/detector_pair.py::judge` runs either side, puts
the active fault set at the front of the oracle's failure message, and reads the recorded
verdict back. The area `conftest.py` sends these verdicts to a per-test file, so invented
defects never land in the run's real verdict evidence.

| Test | Fault switch | Control | Oracle |
|---|---|---|---|
| `test_credit_never_exceeds_paid_detector[fault-on/off]` | `refund_tax_applied_twice` | a closure on the term end credits zero; the closure under test credits something | the credit is at most what was paid for the period (`DEMO-101`) |
| `test_credit_legs_sum_to_total[fault-on/off]` | `refund_legs_rounded_independently` | a closure date exists on which rounding the legs separately and rounding their sum differ | net plus tax equals the total (`DEMO-102`) |

```python
detector_pair.judge(
    demo_control,
    ticket="DEMO-102",
    signature=r"DEMO-102: the credit legs add up to",
    oracle=oracle,
    exercised=exercised,
    fault="refund_legs_rounded_independently",
    fault_on=True,
)
```
