"""Customers: field rules, the account check, issued numbers and the verification gate."""

from __future__ import annotations

import pytest

from demo_sut.customers import (
    MESSAGES,
    NAME_LIMIT,
    IdSequences,
    iban_checksum_ok,
    make_iban,
    validate_postal_code,
)
from demo_sut.seeding import default_address


def individual(**fields):
    return {
        "type": "INDIVIDUAL",
        "display_name": "Rowan Testcase",
        "email": "rowan@example.test",
        "billing_address": default_address(),
        **fields,
    }


def test_a_generated_account_passes_and_a_changed_digit_does_not():
    good = make_iban(bban="UNIT0000000042")
    assert iban_checksum_ok(good)
    assert not iban_checksum_ok(good[:-1] + ("7" if good[-1] != "7" else "8"))


def test_payout_account_check_is_enforced_on_create(demo_sut, demo_control):
    good = make_iban(bban="UNIT0000000099")
    bad = good[:-1] + ("5" if good[-1] != "5" else "6")
    refused = demo_sut.client.post("/api/customers", individual(payout_account=bad))
    assert refused.status == 422
    assert refused.json()["fields"]["payout_account"] == MESSAGES["account_invalid"]
    created = demo_sut.client.ok("POST", "/api/customers", individual(payout_account=good))
    assert demo_sut.client.ok("GET", f"/api/customers/{created['id']}")["payout_account"] == good


@pytest.mark.parametrize(
    ("postal_code", "accepted"),
    [
        ("QX 4410", True),
        ("12345", True),
        ("k-77", True),
        ("A", True),
        ("", False),
        ("  ", False),
        ("-123", False),
        ("12/45", False),
        ("ABCDEFGHIJKLM", False),
    ],
)
def test_postal_codes_are_free_form_within_limits(postal_code, accepted):
    assert (validate_postal_code(postal_code) is None) is accepted


def test_a_bad_postal_code_is_reported_on_its_field(demo_sut, demo_control):
    body = individual(billing_address=default_address(postal_code="12/45"))
    response = demo_sut.client.post("/api/customers", body)
    assert response.status == 422
    assert response.json()["fields"] == {
        "billing_address.postal_code": MESSAGES["postal_code_invalid"]
    }


def test_display_name_limit(demo_sut, demo_control):
    at_limit = "N" * NAME_LIMIT
    created = demo_sut.client.ok("POST", "/api/customers", individual(display_name=at_limit))
    assert created["display_name"] == at_limit
    over = demo_sut.client.post("/api/customers", individual(display_name=at_limit + "N"))
    assert over.status == 422
    assert over.json()["fields"]["display_name"] == MESSAGES["name_too_long"]


@pytest.mark.parametrize(
    ("phone", "accepted"), [("+44 20 7946 0000", True), ("0201234", True), ("call me", False)]
)
def test_phone_is_one_optional_string(demo_sut, demo_control, phone, accepted):
    response = demo_sut.client.post("/api/customers", individual(phone=phone))
    assert (response.status == 201) is accepted
    if not accepted:
        assert response.json()["fields"]["phone"] == MESSAGES["phone_invalid"]


def test_an_unknown_type_is_named(demo_sut, demo_control):
    response = demo_sut.client.post("/api/customers", individual(type="ROBOT"))
    assert response.status == 422
    assert response.json()["fields"]["type"] == MESSAGES["type_invalid"]


def test_numbers_are_issued_by_the_service_and_strictly_increase(demo_sut, demo_control):
    first = demo_sut.client.ok("POST", "/api/customers", individual(number="CUS-D1-1"))
    second = demo_sut.client.ok("POST", "/api/customers", individual())
    for doc in (first, second):
        assert IdSequences.PATTERN.match(doc["number"])
    n1 = int(first["number"].rsplit("-", 1)[1])
    n2 = int(second["number"].rsplit("-", 1)[1])
    assert n2 > n1


def test_unverified_customer_cannot_hold_a_subscription(demo_sut, demo_seed):
    customer = demo_seed.customer(verified=False)
    assert customer["status"] == "pending_verification"
    body = {"customer_id": customer["id"], "partner_id": 1, "plan_id": demo_seed.plan_id()}
    refused = demo_sut.client.post("/api/subscriptions", body)
    assert refused.status == 422
    assert refused.json()["message"] == MESSAGES["not_verified"]
    demo_sut.client.ok("POST", f"/api/customers/{customer['id']}/actions/verify")
    assert demo_sut.client.post("/api/subscriptions", body).status == 201


def test_invoices_by_email_need_an_email_address(demo_sut, demo_seed):
    customer = demo_seed.customer(email=None)
    body = {"customer_id": customer["id"], "partner_id": 1, "plan_id": demo_seed.plan_id()}
    refused = demo_sut.client.post("/api/subscriptions", body)
    assert refused.status == 422
    assert refused.json()["message"] == MESSAGES["email_for_delivery"]
    portal = demo_sut.client.post("/api/subscriptions", {**body, "invoice_delivery": "PORTAL"})
    assert portal.status == 201


def test_patch_revalidates_and_keeps_the_number(demo_sut, demo_seed):
    customer = demo_seed.customer()
    refused = demo_sut.client.patch(f"/api/customers/{customer['id']}", {"display_name": " "})
    assert refused.status == 422
    changed = demo_sut.client.ok(
        "PATCH", f"/api/customers/{customer['id']}", {"display_name": "Renamed Member"}
    )
    assert changed["number"] == customer["number"]
    assert changed["display_name"] == "Renamed Member"


def test_unknown_customer_is_a_service_404(demo_sut):
    response = demo_sut.client.get("/api/customers/999999")
    assert response.status == 404
    assert response.json()["error"] == "not_found"


@pytest.mark.parametrize("fault_on", [False, True])
def test_customer_cache_fault(demo_sut, demo_seed, demo_control, fault_on):
    if fault_on:
        demo_control.fault("customer_read_cached_after_update")
    customer = demo_seed.customer(display_name="Before Rename")
    url = f"/api/customers/{customer['id']}"
    demo_sut.client.ok("GET", url)
    demo_sut.client.ok("PATCH", url, {"display_name": "After Rename"})
    read = demo_sut.client.ok("GET", url)["display_name"]
    listed = next(
        c for c in demo_sut.client.ok("GET", "/api/customers") if c["id"] == customer["id"]
    )
    assert listed["display_name"] == "After Rename"
    assert read == ("Before Rename" if fault_on else "After Rename")


@pytest.mark.parametrize("fault_on", [False, True])
def test_display_name_truncation_fault(demo_sut, demo_control, fault_on):
    if fault_on:
        demo_control.fault("customer_name_truncated")
    name = "L" * 52
    created = demo_sut.client.ok("POST", "/api/customers", individual(display_name=name))
    assert created["display_name"] == (name[:40] if fault_on else name)
    assert demo_sut.client.post("/api/customers", individual(display_name="L" * 65)).status == 422
