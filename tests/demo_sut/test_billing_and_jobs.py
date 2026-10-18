"""Price calculation, invoices and journal, scheduler modes and background jobs."""

from __future__ import annotations

import time

import pytest

from demo_sut.billing import PRICE_MESSAGES, periods

NOW = "2026-03-10T09:00:00Z"


@pytest.fixture
def frozen(demo_control):
    demo_control.freeze(NOW)
    return demo_control


def quote(client, **body):
    return client.post("/api/quotes/calculate", body)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"net_amount": 10, "tax_rate": 0}, {"tax_amount": 0.0, "gross_amount": 10.0}),
        ({"net_amount": "10.005", "tax_rate": 20}, {"net_amount": 10.01, "tax_amount": 2.0}),
        ({"net_amount": 1, "tax_rate": 20}, {"gross_amount": 1.2}),
        ({"net_amount": 10000, "tax_rate": 20}, {"gross_amount": 12000.0}),
        (
            {"net_amount": 100, "tax_rate": 20, "commission_percentage": 20},
            {"commission_amount": 20.0},
        ),
    ],
)
def test_price_calculation_accepts(demo_sut, demo_control, body, expected):
    result = demo_sut.client.ok("POST", "/api/quotes/calculate", body)
    assert {k: result[k] for k in expected} == expected


@pytest.mark.parametrize(
    ("body", "field", "message"),
    [
        ({"net_amount": -1}, "net_amount", PRICE_MESSAGES["negative"]),
        ({"net_amount": 0.5}, "net_amount", PRICE_MESSAGES["minimum"]),
        ({"net_amount": 10000.01}, "net_amount", PRICE_MESSAGES["maximum"]),
        ({"net_amount": 10, "tax_rate": 101}, "tax_rate", PRICE_MESSAGES["tax_rate"]),
        (
            {"net_amount": 10, "commission_percentage": 25},
            "commission_percentage",
            "Commission may not exceed 20%.",
        ),
    ],
)
def test_price_calculation_refuses(demo_sut, demo_control, body, field, message):
    response = quote(demo_sut.client, **body)
    assert response.status == 422
    assert response.json()["fields"] == {field: message}


def test_price_faults(demo_sut, demo_control):
    demo_control.faults("negative_price_accepted", "rounding_drift")
    negative = quote(demo_sut.client, net_amount=-5, tax_rate=20)
    assert negative.status == 200 and negative.json()["net_amount"] == -5.0
    drift = quote(demo_sut.client, net_amount="10.009", tax_rate=20).json()
    assert drift["net_amount"] == 10.0  # truncated instead of 10.01
    assert set(negative.faults) == {"negative_price_accepted", "rounding_drift"}


def test_booking_issues_invoice_and_balanced_journal(demo_sut, demo_seed, frozen):
    client = demo_sut.client
    sub = demo_seed.subscription(start_date="2026-03-01")
    (booked,) = demo_seed.book_due(sub["id"])
    (invoice,) = client.ok("GET", f"/api/subscriptions/{sub['id']}/invoices")
    assert invoice["transaction_id"] == booked and invoice["status"] == "OPEN"
    assert invoice["number"].isdigit() and invoice["document_id"]
    assert invoice["net"] == 52.5 and invoice["tax"] == 10.5  # first period carries the setup fee
    assert invoice["total"] == invoice["net"] + invoice["tax"] == invoice["remaining"] == 63.0
    assert invoice["due_date"] == "2026-04-09" and invoice["reference_date"] == "2026-03-10"
    (entry,) = client.ok("GET", f"/api/invoices/{invoice['id']}/journal")
    debit = sum(line["debit"] for line in entry["lines"])
    credit = sum(line["credit"] for line in entry["lines"])
    assert debit == credit == 63.0
    mapping = client.ok("GET", "/api/ledger-codes")
    fee_line = next(line for line in entry["lines"] if line["role"] == "FEES")
    assert fee_line["account"] == mapping["FEES"] and fee_line["credit"] == 20.0
    tx_ref = f"TX-{booked}"
    assert client.ok("GET", f"/api/journal?references={tx_ref}") == [entry]
    paid = client.ok("POST", f"/api/invoices/{invoice['id']}/pay", {})
    assert paid["status"] == "PAID" and paid["remaining"] == 0
    statement = client.ok("GET", "/api/payments/statement")
    assert statement[-1]["invoice_number"] == invoice["number"]


def test_one_at_a_time_registers_only_the_earliest(demo_sut, demo_seed, frozen):
    frozen.settings(scheduler_mode="one_at_a_time")
    sub = demo_seed.subscription(start_date="2026-03-01")
    txs = demo_seed.transactions(sub["id"])
    assert [t["state"] for t in txs[:3]] == ["SCHEDULED", "INITIAL", "INITIAL"]
    assert txs[0]["job_id"] and txs[1]["job_id"] is None
    demo_sut.client.ok("POST", "/api/jobs/billing/run-next", {"subscription_id": sub["id"]})
    txs = demo_seed.transactions(sub["id"])
    assert [t["state"] for t in txs[:3]] == ["BOOKED", "SCHEDULED", "INITIAL"]


def test_scheduler_mode_route(demo_sut, demo_control):
    assert demo_sut.client.ok("POST", "/api/_admin/scheduler-mode", {"mode": "one_at_a_time"}) == {
        "mode": "one_at_a_time"
    }
    assert demo_sut.client.post("/api/_admin/scheduler-mode", {"mode": "x"}).status == 400


def test_run_due_books_everything_due_and_process_until(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(start_date="2026-01-01")
    booked = demo_seed.book_due(sub["id"])
    assert len(booked) == 3  # Jan, Feb, Mar start on or before 10 March
    result = demo_sut.client.ok(
        "POST", f"/api/subscriptions/{sub['id']}/invoices/process?until=2026-05-01"
    )
    assert len(result["booked"]) == 2  # April and May
    assert (
        demo_sut.client.post("/api/jobs/billing/run-next", {"subscription_id": 999999}).status
        == 404
    )


def test_jobs_are_keyset_paged(demo_sut, demo_seed, frozen):
    demo_seed.subscription()
    first = demo_sut.client.ok("GET", "/api/jobs?limit=5")
    assert len(first["items"]) == 5 and first["next_after"]
    second = demo_sut.client.ok("GET", f"/api/jobs?limit=5&after={first['next_after']}")
    assert second["items"][0]["id"] > first["items"][-1]["id"]
    job = demo_sut.client.ok("GET", f"/api/jobs/{first['items'][0]['id']}")
    assert job["kind"] == "BOOK_TRANSACTION"


def test_generate_runs_as_polled_background_job(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    started = demo_sut.client.post("/api/messages/generate", {"subscription_id": sub["id"]})
    assert started.status == 202
    job_id = started.json()["job_id"]
    assert started.headers["location"] == f"/api/jobs/{job_id}"
    states = []
    for _ in range(100):
        state = demo_sut.client.ok("GET", f"/api/jobs/{job_id}")["state"]
        states.append(state)
        if state == "DONE":
            break
        time.sleep(0.05)
    assert states[0] == "RUNNING" and states[-1] == "DONE"


def test_periods_clip_at_the_end():
    from datetime import date

    out = periods(date(2026, 1, 31), date(2026, 4, 15), "MONTHLY")
    assert out[0] == (date(2026, 1, 31), date(2026, 2, 28))
    assert out[-1] == (date(2026, 3, 31), date(2026, 4, 15))


def test_a_quote_from_a_plan_uses_its_base_price(demo_sut, demo_control):
    plan = demo_sut.client.ok("GET", "/api/plans")[0]
    quote_ = demo_sut.client.ok("POST", "/api/quotes/calculate", {"plan_id": plan["id"]})
    assert quote_["net_amount"] == plan["base_monthly_net"]
    assert quote_["tax_rate"] == plan["tax_rate"]


def test_confirm_response_carries_the_schedule(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(state="pending_payment", start_date="2026-03-01")
    confirmed = demo_sut.client.ok("POST", f"/api/subscriptions/{sub['id']}/actions/confirm")
    schedule = confirmed["scheduled_transactions"]
    assert len(schedule) == 12 and schedule == demo_seed.transactions(sub["id"])


@pytest.mark.parametrize("fault_on", [False, True])
def test_payment_replay_fault(demo_sut, demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("payment_replay_double_counted")
    sub = demo_seed.subscription(start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    (invoice,) = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/invoices")
    headers = {"Idempotency-Key": f"pay-{sub['id']}"}
    first = demo_sut.client.post(
        f"/api/invoices/{invoice['id']}/pay", {"amount": 10}, headers=headers
    )
    again = demo_sut.client.post(
        f"/api/invoices/{invoice['id']}/pay", {"amount": 10}, headers=headers
    )
    assert first.status == again.status == 200
    same = first.json()["payment"]["id"] == again.json()["payment"]["id"]
    assert same is not fault_on
    remaining = demo_sut.client.ok("GET", f"/api/invoices/{invoice['id']}")["remaining"]
    assert remaining == round(invoice["total"] - (20 if fault_on else 10), 2)


@pytest.mark.parametrize("fault_on", [False, True])
def test_run_next_rebook_fault(demo_sut, demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("run_next_rebooks_claimed_job")
    sub = demo_seed.subscription(start_date="2026-03-01")
    for _ in range(2):
        demo_sut.client.ok("POST", "/api/jobs/billing/run-next", {"subscription_id": sub["id"]})
    invoices = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/invoices")
    by_tx = {i["transaction_id"] for i in invoices}
    assert len(invoices) == 2
    assert (len(by_tx) == 1) is fault_on
