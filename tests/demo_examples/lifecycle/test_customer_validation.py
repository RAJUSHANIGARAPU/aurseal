"""Customer rules at creation: the payout account check, e-mail for e-mail delivery, and
long display names kept whole."""

from __future__ import annotations

import pytest

from demo_sut.customers import make_iban
from demo_sut.seeding import default_address
from tests.demo_examples import detector_pair
from tests.demo_examples.lifecycle import support


def member(**fields):
    return {
        "type": "INDIVIDUAL",
        "display_name": "Rule Reader",
        "email": "rule.reader@example.test",
        "billing_address": default_address(),
        **fields,
    }


def test_payout_account_check_digits_at_creation(demo_http, frozen):
    valid = make_iban(bban="RULE00000077")
    broken = valid[:2] + f"{(int(valid[2:4]) + 1) % 100:02d}" + valid[4:]

    refused = demo_http.post("customers", json=member(payout_account=broken))
    assert refused.status == 422
    assert refused.json()["fields"] == {"payout_account": "Account number fails its check digits."}

    spaced = " ".join(valid[i : i + 4] for i in range(0, len(valid), 4))
    created = support.ok(demo_http.post("customers", json=member(payout_account=spaced)))
    assert support.ok(demo_http.get(f"customers/{created['id']}"))["payout_account"] == valid


def test_email_delivery_needs_an_email_on_the_customer(demo_http, demo_seed, frozen):
    without = demo_seed.customer(email=None)
    body = {"customer_id": without["id"], "partner_id": 1, "plan_id": demo_seed.plan_id()}
    refused = demo_http.post("subscriptions", json=body)
    assert refused.status == 422
    assert refused.json()["fields"] == {
        "invoice_delivery": "Invoices by e-mail need an e-mail address."
    }
    portal = support.ok(
        demo_http.post("subscriptions", json={**body, "invoice_delivery": "PORTAL"})
    )
    assert portal["invoice_delivery"] == "PORTAL"


DISPLAY_TICKET = "DEMO-112"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_long_display_name_kept_detector(demo_http, demo_control, frozen, fault_on):
    demo_control.faults(*(["customer_name_truncated"] if fault_on else []))
    long_names = {n: ("Q" * (n - 3)) + f"{n:03d}" for n in (41, 52, 64)}
    stored = {
        n: support.ok(demo_http.post("customers", json=member(display_name=name)))["display_name"]
        for n, name in long_names.items()
    }
    over = demo_http.post("customers", json=member(display_name="Q" * 65))

    def exercised() -> None:
        at_forty = "R" * 40
        kept = support.ok(demo_http.post("customers", json=member(display_name=at_forty)))
        assert kept["display_name"] == at_forty, "control: a 40-character name reads back"
        assert over.status == 422, "the documented limit refuses 65 characters"

    def oracle() -> None:
        cut = {n: len(stored[n]) for n in long_names if stored[n] != long_names[n]}
        assert not cut, f"{DISPLAY_TICKET}: display names were shortened without an error: {cut}"

    detector_pair.judge(
        demo_control,
        ticket=DISPLAY_TICKET,
        signature=rf"{DISPLAY_TICKET}: display names were shortened without an error",
        oracle=oracle,
        exercised=exercised,
        fault="customer_name_truncated",
        fault_on=fault_on,
    )
