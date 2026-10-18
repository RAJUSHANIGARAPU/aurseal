# Demo examples: scheduler and billing

Examples under `tests/demo_examples/scheduler/` that drive the demo's booking jobs (see
[demo-sut.md](demo-sut.md) and [demo-examples.md](demo-examples.md)). They are API-level and
run in the offline suite. Every example freezes the demo clock at 2026-03-10 09:00 UTC
(`frozen` fixture) and seeds its own subscriptions.

## Synchronous billing client

**What it does.** `api.clients.billing_runner.BillingRunner` books a billing schedule without
waiting for the background scheduler. It runs over the project's `HttpEngine`:

| Method | Does |
|---|---|
| `process_next_due(entity_id)` | books the next scheduled transaction now; `None` when nothing is scheduled |
| `process_all_due(entity_id, limit=100)` | repeats that until nothing is left; a chain longer than `limit` fails |
| `run_due(until=None, entity_id=None)` | the scheduler's own route: books what is due on or before `until` |
| `transactions(entity_id)` | the schedule |
| `await_transaction(entity_id, kind=, states=, where=, timeout_s=30, interval_s=0.5)` | polls until a transaction matches; on timeout the failure lists what it last saw |

A refusal other than "nothing left" raises `BillingError` quoting the response.

**Configuration.** Constructor arguments: `run_next_path` (`jobs/billing/run-next`),
`run_due_path` (`jobs/run`), `transactions_path` (`subscriptions/{id}/transactions`),
`entity_field` (`subscription_id`), `nothing_due_statuses` (409, 422), `state_field`,
`kind_field`.

**Example.** `test_synchronous_billing.py::test_bills_first_then_all_without_the_scheduler`
starts a monthly subscription in June. The scheduler route books nothing for it today.
`process_next_due` books the June period in the same request, and `process_all_due` books the
other eleven. The states read back as twelve `BOOKED`, and twelve invoices exist.

```python
billing = BillingRunner(demo_http)
billing.process_next_due(sub["id"])
billing.process_all_due(sub["id"])
```

## Process flip probe

**What it shows.** `test_scheduler_probes.py::test_process_flip_probe` binds a yearly
subscription and processes its latest scheduled transaction through the job route
(`POST /api/jobs/run` with `until` the day after its period start). It then polls until the
transaction flips to `BOOKED` and records the time taken as the `probe_flip_seconds` user
property. The probe fails only when it cannot run (nothing schedulable).

## Yearly term renewal reaches pricing

**What it shows.** `test_yearly_term_renewal_reaches_pricing` books the single yearly
installment synchronously. It requires exactly one transaction with nothing unbooked before
the term end, and abstains otherwise. It then opens a renewal draft and asserts the draft
reaches the `price` step with an indexed price for the new term. The draft is discarded in a
`finally` block, and the subscription is back to `active`.

## Same-day cancellation is processed

**What it shows.** `test_same_day_cancellation_processed` cancels a booked subscription
effective today and waits for the `CANCEL` transaction (`await_transaction`). It then runs the
scheduler's own route. Control: the route must be accepted, else the test skips. Oracle: the
cancellation transaction is not left `SCHEDULED` and ends `BOOKED`.

## One-at-a-time registration rules

**What it shows.** `test_scheduler_registration.py` switches the demo to one-at-a-time
registration (`POST /api/_admin/scheduler-mode`). It reads schedules through a
`ReadOnlyClient` and judges them with `oracles.job_registration`.

* `test_registration_rules_abstain_while_every_transaction_is_registered`: in the default
  mode every transaction has a job, and the rules abstain (skip) instead of failing.
* `test_one_at_a_time_registration_rules`: a bound monthly subscription has exactly one
  `SCHEDULED` transaction (the earliest, with a job due on its period start) and the rest
  `INITIAL`. Booking it registers exactly the next one, and that job is due on its own
  period start.

## Each transaction is booked once (detector)

**What it shows.** `test_run_next.py::test_each_transaction_is_booked_once_detector` is an
expected-red detector (`DEMO-110`) run as a pair with the demo fault
`run_next_rebooks_claimed_job`. It calls `POST /api/jobs/billing/run-next` twice on the same
clock tick and counts invoices per transaction. The oracle requires no transaction to be
booked more than once. The positive control requires one call on another subscription to
book exactly one transaction, and the two calls under test to issue two invoices. With the
fault on the verdict is `reproduces`; with it off the detector reports
`KNOWN DEFECT CLEARED` and the test asserts it.

`test_scheduler_registration.py::test_the_chain_probe_cannot_write` checks the read-only
probe module's source with `gate.read_only.find_write_calls`.

```bash
pytest tests/demo_examples/scheduler -n auto
```
