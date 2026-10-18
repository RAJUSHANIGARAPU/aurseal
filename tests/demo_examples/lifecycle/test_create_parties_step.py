"""Creating a subscription: parties picked by number and bound, and a throttled create sent
again only when the refusal provably stored nothing."""

from __future__ import annotations

import uuid

import pytest

from api.clients.http_engine import ApiResponse
from test_data.parties import AmbiguousMatch, select_unique
from tests.demo_examples.lifecycle import support

by_number = {"name_of": lambda r: str(r.get("number") or "")}


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_parties_step_binds_by_number_and_resends_a_throttled_create(
    demo_http, demo_seed, demo_control, frozen, fault_on
):
    same_name = f"Namesake {uuid.uuid4().hex[:6]}"
    demo_seed.customer(display_name=same_name)
    wanted_customer = demo_seed.customer(display_name=same_name)
    demo_seed.partner(name=same_name)
    wanted_partner = demo_seed.partner(name=same_name)

    customers = support.ok(demo_http.get("customers"))
    partners = support.ok(demo_http.get("partners"))
    names = {"name_of": lambda r: str(r.get("display_name") or "")}
    with pytest.raises(AmbiguousMatch):  # a display name does not identify a party
        select_unique(customers, "customer", same_name, **names)
    customer = select_unique(customers, "customer", wanted_customer["number"], **by_number)
    partner = select_unique(partners, "partner", wanted_partner["number"], **by_number)

    demo_control.faults(*(["create_throttled_every_other"] if fault_on else []))
    body = {
        "customer_id": customer["id"],
        "partner_id": partner["id"],
        "plan_id": demo_seed.plan_id(),
    }
    attempts = []
    for _ in range(2):  # with the fault, two intended creates always meet one 429
        response, took = support.retry_create(lambda: demo_http.post("subscriptions", json=body))
        attempts.append(took)
    assert (sum(attempts) > 2) is fault_on, f"attempts per create: {attempts}"
    found = support.ok(
        demo_http.post("search", json={"resource": "subscriptions", "text": same_name})
    )
    assert found["total"] == 2, "exactly one subscription per intended create"
    sub = response.json()

    # each party field is bound to the record picked, not merely non-empty
    assert sub["holder"]["number"] == wanted_customer["number"]
    assert sub["partner"]["number"] == wanted_partner["number"]
    sub = support.ok(demo_http.patch(f"subscriptions/{sub['id']}", json={"frequency": "MONTHLY"}))
    submitted = support.ok(demo_http.post(f"subscriptions/{sub['id']}/actions/submit", json={}))
    assert submitted["status"] == "pending_payment"


def test_only_a_throttle_is_resent_and_never_more_than_allowed():
    calls: list[int] = []
    waits: list[float] = []

    def always(status, headers=None):
        def create():
            calls.append(status)
            return ApiResponse(status, b'{"error": "x"}', headers=headers or {})

        return create

    with pytest.raises(AssertionError, match=r"attempt 3 of 3"):
        support.retry_create(always(429, {"Retry-After": "0"}), attempts=3, sleep=waits.append)
    assert calls.count(429) == 3 and waits == [0.0, 0.0, 0.0]
    for status in (409, 422, 504):
        with pytest.raises(AssertionError, match=r"attempt 1 of 3"):
            support.retry_create(always(status), attempts=3, sleep=waits.append)
        assert calls.count(status) == 1, f"a {status} must never be resent"
