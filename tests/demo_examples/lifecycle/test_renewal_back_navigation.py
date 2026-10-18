"""Renewal indexation step: readers, read-only dates, and the back/forward guard."""

from __future__ import annotations

from decimal import Decimal as D

from oracles.charges import indexed_amount
from tests.demo_examples.lifecycle import support
from tests.demo_examples.lifecycle.renewal_step import (
    RenewalStep,
    lost_after_round_trip,
    read_only_changed,
)


def test_indexation_step_reads_holds_dates_and_survives_back_forward(
    demo_http, frozen, seed_renewable_subscription
):
    seeded = seed_renewable_subscription()
    draft = support.open_renewal(demo_http, seeded["subscription_id"])
    first = RenewalStep.read(draft)
    assert None not in first.as_dict().values(), first
    assert first.old_term_end == seeded["term_end"]
    assert first.new_price == indexed_amount(first.current_price, first.index_percent / 100)

    path = f"drafts/{draft['id']}"
    # the dates are read-only: sending them changes nothing
    tampered = support.ok(
        demo_http.patch(path, json={"old_term_end": "2030-01-01", "new_term_end": "2030-01-01"})
    )
    assert read_only_changed(first, RenewalStep.read(tampered)) == []

    # Next with a new index, then Back to the original: every value is still there
    forward = RenewalStep.read(support.ok(demo_http.patch(path, json={"index_percent": 4})))
    assert forward.index_percent == D(4)
    assert forward.new_price == indexed_amount(first.current_price, D("0.04"))
    back = RenewalStep.read(
        support.ok(demo_http.patch(path, json={"index_percent": float(first.index_percent)}))
    )
    assert lost_after_round_trip(first, back) == []
    assert back == first


def test_guard_names_a_value_lost_on_back_navigation():
    before = RenewalStep(D("2.5"), "2026-03-10", "2027-03-10", D(40), D(41))
    stuck = RenewalStep(D("2.5"), "2026-03-10", None, D(40), D(41))
    assert lost_after_round_trip(before, stuck) == [
        "new_term_end lost on back/forward (was 2027-03-10)"
    ]
    assert read_only_changed(before, stuck) == ["new_term_end changed 2027-03-10 -> None"]
