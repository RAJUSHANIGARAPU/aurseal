"""Booking the next transaction books each transaction once (detector pair)."""

from __future__ import annotations

import pytest

from tests.demo_examples import detector_pair

TICKET = "DEMO-110"


def run_next(demo_sut, sub_id: int) -> dict:
    return demo_sut.client.ok("POST", "/api/jobs/billing/run-next", {"subscription_id": sub_id})


def invoices_by_transaction(demo_sut, sub_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    for invoice in demo_sut.client.ok("GET", f"/api/subscriptions/{sub_id}/invoices"):
        counts[invoice["transaction_id"]] = counts.get(invoice["transaction_id"], 0) + 1
    return counts


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_each_transaction_is_booked_once_detector(demo_sut, demo_seed, demo_control, fault_on):
    demo_control.freeze("2026-06-03T09:00:00Z")
    demo_control.faults(*(["run_next_rebooks_claimed_job"] if fault_on else []))
    sub = demo_seed.subscription(start_date="2026-06-01")
    run_next(demo_sut, sub["id"])
    run_next(demo_sut, sub["id"])  # the same clock tick
    counts = invoices_by_transaction(demo_sut, sub["id"])

    def exercised() -> None:
        # one call books exactly one transaction, with or without the fault
        other = demo_seed.subscription(start_date="2026-06-01")
        run_next(demo_sut, other["id"])
        assert list(invoices_by_transaction(demo_sut, other["id"]).values()) == [1]
        assert sum(counts.values()) == 2, f"two calls should issue two invoices: {counts}"

    def oracle() -> None:
        doubled = {tx: n for tx, n in counts.items() if n > 1}
        assert not doubled, f"{TICKET}: a transaction was booked more than once: {doubled}"

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: a transaction was booked more than once",
        oracle=oracle,
        exercised=exercised,
        fault="run_next_rebooks_claimed_job",
        fault_on=fault_on,
    )
