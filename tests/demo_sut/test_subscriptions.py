"""Subscriptions: lifecycle, versions, drafts, pro-rata, credits, renewals and search."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from demo_sut.customers import make_iban
from demo_sut.subscriptions import LOCKED_IN_CHANGE, MESSAGES, next_term_end

NOW = "2026-03-10T09:00:00Z"
CENT = Decimal("0.01")


def cents(value) -> float:
    return float(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


@pytest.fixture
def frozen(demo_control):
    demo_control.freeze(NOW)
    return demo_control


def of_kind(txs, kind):
    return [t for t in txs if t["kind"] == kind]


def booked_march(demo_seed, **fields):
    sub = demo_seed.subscription(start_date="2026-03-01", **fields)
    demo_seed.book_due(sub["id"])
    return sub


# -- lifecycle ---------------------------------------------------------------


def test_draft_to_pending_to_active(demo_sut, demo_seed, frozen):
    client = demo_sut.client
    sub = demo_seed.subscription(state="draft", start_date="2026-03-01")
    assert sub["status"] == "draft" and sub["number"].startswith("SUB-")
    assert sub["currency"] == "EUR" and sub["data_region"] == "EU-CENTRAL"
    assert demo_seed.transactions(sub["id"]) == []
    pending = client.ok(
        "POST", f"/api/subscriptions/{sub['id']}/actions/submit", {"net_amount": 36, "tax_rate": 20}
    )
    assert pending["status"] == "pending_payment"
    assert pending["price"]["monthly_net"] == 36.0
    active = client.ok("POST", f"/api/subscriptions/{sub['id']}/actions/confirm")
    assert active["status"] == "active"
    txs = demo_seed.transactions(sub["id"])
    assert len(txs) == 12 and {t["state"] for t in txs} == {"SCHEDULED"}
    setup_fee = client.ok("GET", f"/api/plans/{sub['plan_id']}")["setup_fee"]
    assert txs[0]["fee_net"] == setup_fee and all(t["fee_net"] == 0 for t in txs[1:])
    templates = [
        d["template"] for d in client.ok("GET", f"/api/subscriptions/{sub['id']}/documents")
    ]
    assert templates == ["order_summary", "account_opened"]


def test_activate_immediately_skips_the_order_summary(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(state="draft", activate_immediately=True)
    assert sub["status"] == "active"
    docs = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/documents")
    assert [d["template"] for d in docs] == ["account_opened"]


def test_add_ons_do_not_change_the_price(demo_seed, frozen):
    plain = demo_seed.subscription(state="draft")
    extras = demo_seed.subscription(state="draft", addons={"sso": True, "sandbox": True})
    assert extras["addons"] == {"sso": True, "audit_trail": False, "sandbox": True}
    assert extras["price"] == plain["price"]


def test_seat_total_is_the_sum_of_the_blocks(demo_sut, demo_seed, frozen):
    blocks = [{"name": "Analysts", "seats": 4}, {"name": "Field crew", "seats": 9}]
    sub = demo_seed.subscription(state="draft", seat_blocks=blocks)
    assert sub["seat_total"] == 13 and sub["seat_blocks"] == blocks
    bad = demo_sut.client.post(
        "/api/subscriptions",
        {**_body(demo_seed), "seat_blocks": [{"name": "", "seats": 0}]},
    )
    assert bad.status == 422 and bad.json()["message"] == MESSAGES["seats_invalid"]


def _body(demo_seed):
    return {
        "customer_id": demo_seed.customer()["id"],
        "partner_id": 1,
        "plan_id": demo_seed.plan_id(),
    }


@pytest.mark.parametrize(
    ("method", "accepted", "field"),
    [
        ({"type": "CARD"}, True, None),
        ({"type": "BANK_TRANSFER"}, True, None),
        ({"type": "DIRECT_DEBIT"}, False, "payment_method.account"),
        ({"type": "DIRECT_DEBIT", "account": make_iban(bban="UNIT0000000501")}, True, None),
        ({"type": "CHEQUE"}, False, "payment_method.type"),
    ],
)
def test_payment_method_rules(demo_sut, demo_seed, frozen, method, accepted, field):
    response = demo_sut.client.post(
        "/api/subscriptions", {**_body(demo_seed), "payment_method": method}
    )
    assert (response.status == 201) is accepted, response.json()
    if field:
        assert field in response.json()["fields"]


def test_po_reference_limit_and_label_catalogue(demo_sut, demo_seed, frozen):
    body = _body(demo_seed)
    assert (
        demo_sut.client.post("/api/subscriptions", {**body, "po_reference": "P" * 35}).status == 201
    )
    too_long = demo_sut.client.post("/api/subscriptions", {**body, "po_reference": "P" * 36})
    assert too_long.status == 422 and too_long.json()["message"] == MESSAGES["po_too_long"]
    labelled = demo_sut.client.ok("POST", "/api/subscriptions", {**body, "labels": ["L-PILOT"]})
    assert labelled["labels"] == [{"code": "L-PILOT", "title": "Pilot"}]
    unknown = demo_sut.client.post("/api/subscriptions", {**body, "labels": ["L-NOPE"]})
    assert unknown.status == 422 and unknown.json()["message"] == MESSAGES["label_unknown"]


def test_the_holder_is_read_from_the_current_customer_record(demo_sut, demo_seed, frozen):
    customer = demo_seed.customer()
    sub = demo_seed.subscription(customer=customer)
    demo_sut.client.ok("PATCH", f"/api/customers/{customer['id']}", {"display_name": "Later Name"})
    assert (
        demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}")["holder"]["name"]
        == "Later Name"
    )


# -- changes -----------------------------------------------------------------


def test_future_change_keeps_the_current_version_until_its_date(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(start_date="2026-03-01")
    draft = demo_seed.draft(
        sub["id"], "change", effective_date="2026-05-01", changes={"monthly_net": 41}
    )
    changed = demo_seed.confirm_draft(draft["id"])
    assert changed["version"] == 2 and changed["version_status"] == "scheduled"
    now = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}")
    assert now["version"] == 1 and now["current"] is True
    frozen.freeze("2026-05-02T09:00:00Z")
    later = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}")
    assert later["version"] == 2 and later["price"]["monthly_net"] == 41.0


def test_mid_period_change_books_the_pro_rata_delta(demo_seed, frozen):
    sub = booked_march(demo_seed)
    old = Decimal(str(sub["price"]["monthly_net"]))
    draft = demo_seed.draft(
        sub["id"], "change", effective_date="2026-03-16", changes={"monthly_net": float(old + 15)}
    )
    demo_seed.confirm_draft(draft["id"])
    change = of_kind(demo_seed.transactions(sub["id"]), "CHANGE")
    assert len(change) == 1
    assert change[0]["net"] == cents(Decimal(15) * 16 / 31)
    assert change[0]["period_start"] == "2026-03-16" and change[0]["period_end"] == "2026-04-01"


def test_a_second_draft_is_refused_with_409(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription()
    first = demo_seed.draft(sub["id"], "change")
    second = demo_sut.client.post(f"/api/subscriptions/{sub['id']}/drafts", {"kind": "cancel"})
    assert second.status == 409
    assert second.json()["draft_id"] == first["id"]


@pytest.mark.parametrize("field", LOCKED_IN_CHANGE)
def test_locked_fields_cannot_be_edited_in_a_change(demo_sut, demo_seed, frozen, field):
    sub = demo_seed.subscription()
    value = {"currency": "USD", "plan_id": 2, "frequency": "YEARLY"}[field]
    response = demo_sut.client.post(
        f"/api/subscriptions/{sub['id']}/drafts", {"kind": "change", "changes": {field: value}}
    )
    assert response.status == 422
    assert response.json()["fields"] == {field: f"{field} cannot be edited in a change."}


def test_a_note_keeps_its_title_and_does_not_block_other_drafts(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription()
    note = demo_seed.draft(sub["id"], "note", title="Kick-off call", description="Booked")
    demo_seed.confirm_draft(note["id"])
    notes = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/notes")
    assert [n["title"] for n in notes] == ["Kick-off call"]
    assert demo_seed.draft(sub["id"], "change")["status"] == "OPEN"
    untitled = demo_seed.draft(sub["id"], "note")
    refused = demo_sut.client.post(f"/api/drafts/{untitled['id']}/actions/confirm")
    assert refused.status == 422 and refused.json()["message"] == MESSAGES["title_required"]


def test_an_add_on_flip_moves_only_that_add_on(demo_seed, frozen):
    sub = demo_seed.subscription(addons={"audit_trail": True})
    draft = demo_seed.draft(sub["id"], "change", changes={"addons": {"sso": True}})
    changed = demo_seed.confirm_draft(draft["id"])
    assert changed["addons"] == {"sso": True, "audit_trail": True, "sandbox": False}


def test_an_invoice_note_is_amended_in_place(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(invoice_notes=[{"text": "first text"}])
    draft = demo_seed.draft(
        sub["id"], "change", changes={"amend_invoice_notes": [{"number": 1, "text": "second text"}]}
    )
    changed = demo_seed.confirm_draft(draft["id"])
    assert changed["invoice_notes"] == [{"number": 1, "version": 2, "text": "second text"}]
    refused = demo_sut.client.post(
        f"/api/subscriptions/{sub['id']}/drafts",
        {"kind": "change", "changes": {"amend_invoice_notes": [{"number": 7, "text": "x"}]}},
    )
    assert refused.status == 422
    assert refused.json()["message"] == MESSAGES["note_unknown"].format(number=7)


def test_an_ownership_transfer_needs_acceptance(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription()
    other = demo_seed.customer()
    same_owner = demo_seed.draft(sub["id"], "change", changes={"customer_id": sub["customer_id"]})
    assert "ownership_acceptance" not in same_owner["steps"]
    demo_sut.client.ok("POST", f"/api/drafts/{same_owner['id']}/actions/discard")
    draft = demo_seed.draft(sub["id"], "change", changes={"customer_id": other["id"]})
    assert draft["steps"][-1] == "ownership_acceptance"
    refused = demo_sut.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert refused.status == 422 and refused.json()["message"] == MESSAGES["acceptance_open"]
    accepted = demo_sut.client.ok("POST", f"/api/drafts/{draft['id']}/actions/accept-ownership")
    assert accepted["ownership_accepted"] is True
    changed = demo_seed.confirm_draft(draft["id"])
    assert changed["customer_id"] == other["id"] and changed["holder"]["id"] == other["id"]


def test_the_start_date_moves_until_the_first_booking(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(start_date="2026-04-01")
    draft = demo_seed.draft(sub["id"], "change", changes={"start_date": "2026-04-20"})
    moved = demo_seed.confirm_draft(draft["id"])
    assert moved["start_date"] == "2026-04-20" and moved["term_end"] == "2027-04-20"
    booked = booked_march(demo_seed)
    refused = demo_sut.client.post(
        f"/api/subscriptions/{booked['id']}/drafts",
        {"kind": "change", "changes": {"start_date": "2026-03-05"}},
    )
    draft_id = refused.json()["id"]
    response = demo_sut.client.post(f"/api/drafts/{draft_id}/actions/confirm")
    assert response.status == 422 and response.json()["message"] == MESSAGES["start_locked"]


# -- renewal -----------------------------------------------------------------


def test_renewal_indexes_the_price_and_extends_the_term_one_year(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(start_date="2025-03-15", frequency="YEARLY")
    demo_seed.book_due(sub["id"])
    draft = demo_seed.draft(sub["id"], "renew", index_percent=4)
    old = Decimal(str(sub["price"]["monthly_net"]))
    assert draft["preview"]["new_monthly_net"] == cents(old * Decimal("1.04"))
    renewed = demo_seed.confirm_draft(draft["id"])
    assert renewed["status"] == "renewed"
    assert renewed["term_start"] == "2026-03-15" and renewed["term_end"] == "2027-03-15"
    assert of_kind(demo_seed.transactions(sub["id"]), "RENEW")
    docs = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/documents")
    assert docs[-1]["template"] == "term_extension_notice"


def test_renewal_preconditions(demo_sut, demo_seed, frozen):
    young = demo_seed.subscription(start_date="2026-03-01")
    too_early = demo_sut.client.post(f"/api/subscriptions/{young['id']}/drafts", {"kind": "renew"})
    assert too_early.status == 422 and too_early.json()["message"] == MESSAGES["renewal_not_due"]
    unbooked = demo_seed.subscription(start_date="2025-03-15", frequency="YEARLY")
    response = demo_sut.client.post(
        f"/api/subscriptions/{unbooked['id']}/drafts", {"kind": "renew"}
    )
    assert response.status == 422 and response.json()["message"] == MESSAGES["renewal_unbooked"]


def test_the_renewal_job_renews_due_subscriptions(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription(start_date="2025-03-15", frequency="YEARLY")
    demo_seed.book_due(sub["id"])
    result = demo_sut.client.ok("POST", "/api/jobs/renewals/run")
    assert sub["id"] in result["renewed"]
    assert demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}")["status"] == "renewed"


def test_next_term_end_clamps_a_leap_day():
    assert next_term_end(date(2028, 2, 29)) == date(2029, 2, 28)
    assert next_term_end(date(2026, 7, 9)) == date(2027, 7, 9)


# -- closure -----------------------------------------------------------------


def test_closure_credits_the_unearned_part_and_keeps_the_reason(demo_sut, demo_seed, frozen):
    sub = booked_march(demo_seed)
    net = Decimal(str(sub["price"]["period_net"]))
    tax = Decimal(str(sub["price"]["period_tax"]))
    draft = demo_seed.draft(sub["id"], "cancel", effective_date="2026-03-16", reason="BUDGET")
    closed = demo_seed.confirm_draft(draft["id"])
    assert closed["status"] == "cancelled"
    closure = closed["closure"]
    assert closure["reason"] == "BUDGET"
    assert closure["credit_net"] == -cents(net * 16 / 31)
    assert closure["credit_tax"] == -cents(tax * 16 / 31)
    assert closure["credit_total"] == cents(
        Decimal(str(closure["credit_net"])) + Decimal(str(closure["credit_tax"]))
    )
    later = [t for t in demo_seed.transactions(sub["id"]) if t["period_start"] >= "2026-04-01"]
    assert later and all(t["state"] == "VOID" for t in later if t["kind"] == "NEW")
    docs = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/documents")
    assert docs[-1]["template"] == "closure_notice"


def test_the_day_count_setting_changes_the_credit(demo_seed, frozen):
    frozen.settings(day_count="30/360")
    sub = booked_march(demo_seed)
    net = Decimal(str(sub["price"]["period_net"]))
    draft = demo_seed.draft(sub["id"], "cancel", effective_date="2026-03-16", reason="OTHER")
    closed = demo_seed.confirm_draft(draft["id"])
    assert closed["closure"]["credit_net"] == -cents(net * 15 / 30)


@pytest.mark.parametrize("reason", ["NO_LONGER_NEEDED", "BUDGET", "CONSOLIDATING", "OTHER"])
def test_every_listed_reason_is_accepted(demo_seed, frozen, reason):
    sub = demo_seed.subscription()
    draft = demo_seed.draft(sub["id"], "cancel", reason=reason)
    assert demo_seed.confirm_draft(draft["id"])["closure"]["reason"] == reason


def test_closure_validations(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription()
    draft = demo_seed.draft(sub["id"], "cancel", reason="BORED")
    refused = demo_sut.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert refused.status == 422 and refused.json()["message"] == MESSAGES["reason_invalid"]
    no_payee = booked_march(demo_seed, customer=demo_seed.customer(payout_account=None))
    draft = demo_seed.draft(no_payee["id"], "cancel", effective_date="2026-03-16", reason="OTHER")
    refused = demo_sut.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert refused.status == 422 and refused.json()["message"] == MESSAGES["no_payee"]


# -- search and versions -------------------------------------------------------


def test_keyset_search_pages_without_duplicates(demo_sut, demo_seed, frozen):
    token = uuid.uuid4().hex[:8]
    for i in range(3):
        demo_seed.subscription(customer=demo_seed.customer(display_name=f"Pager {token} {i}"))
    client = demo_sut.client
    first = client.ok("POST", "/api/search", {"text": token, "limit": 2})
    assert first["total"] == 3 and len(first["items"]) == 2 and first["prev_cursor"] is None
    second = client.ok(
        "POST", "/api/search", {"text": token, "limit": 2, "after": first["next_cursor"]}
    )
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    ids = [i["id"] for i in first["items"] + second["items"]]
    assert len(set(ids)) == 3 and ids == sorted(ids)
    back = client.ok(
        "POST", "/api/search", {"text": token, "limit": 2, "before": second["prev_cursor"]}
    )
    assert [i["id"] for i in back["items"]] == [i["id"] for i in first["items"]]
    assert client.post("/api/search", {"after": "not-a-cursor"}).status == 400


def test_versions_endpoint_and_an_unknown_version(demo_sut, demo_seed, frozen):
    sub = demo_seed.subscription()
    draft = demo_seed.draft(sub["id"], "change", changes={"po_reference": "PO-UNIT-1"})
    demo_seed.confirm_draft(draft["id"])
    versions = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/versions")
    assert [v["version"] for v in versions] == [1, 2]
    assert [v["current"] for v in versions] == [False, True]
    assert demo_sut.client.get(f"/api/subscriptions/{sub['id']}?version=9").status == 404


# -- fault switches: each one changes the behaviour it names ----------------------------------


def _close_on(demo_seed, effective: str, **fields):
    sub = booked_march(demo_seed, **fields)
    draft = demo_seed.draft(sub["id"], "cancel", effective_date=effective, reason="BUDGET")
    return sub, demo_seed.confirm_draft(draft["id"])


@pytest.mark.parametrize("fault_on", [False, True])
def test_credit_tax_fault(demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("refund_tax_applied_twice")
    sub, closed = _close_on(demo_seed, "2026-03-01")  # the whole booked March is unearned
    paid = Decimal(str(sub["price"]["period_net"])) + Decimal(str(sub["price"]["period_tax"]))
    credit = -Decimal(str(closed["closure"]["credit_total"]))
    assert (credit > paid) is fault_on, (credit, paid)


@pytest.mark.parametrize("fault_on", [False, True])
def test_credit_leg_rounding_fault(demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("refund_legs_rounded_independently")
    sub = booked_march(demo_seed)
    net, tax = (Decimal(str(sub["price"][k])) for k in ("period_net", "period_tax"))
    day = next(
        d
        for d in range(2, 31)
        if cents(net * (32 - d) / 31) + cents(tax * (32 - d) / 31)
        != cents(net * (32 - d) / 31 + tax * (32 - d) / 31)
    )
    draft = demo_seed.draft(
        sub["id"], "cancel", effective_date=f"2026-03-{day:02d}", reason="OTHER"
    )
    closure = demo_seed.confirm_draft(draft["id"])["closure"]
    legs = Decimal(str(closure["credit_net"])) + Decimal(str(closure["credit_tax"]))
    assert (legs != Decimal(str(closure["credit_total"]))) is fault_on


@pytest.mark.parametrize("fault_on", [False, True])
def test_proration_floor_fault(demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("proration_floors_to_zero")
    sub = booked_march(demo_seed)
    old = Decimal(str(sub["price"]["monthly_net"]))
    draft = demo_seed.draft(
        sub["id"], "change", effective_date="2026-03-16", changes={"monthly_net": float(old + 6)}
    )
    demo_seed.confirm_draft(draft["id"])
    (change,) = of_kind(demo_seed.transactions(sub["id"]), "CHANGE")
    assert (change["net"] == 0) is fault_on


@pytest.mark.parametrize(("fault_on", "expected"), [(False, "2026-04-03"), (True, "2026-03-04")])
def test_start_date_swap_fault(demo_seed, frozen, fault_on, expected):
    if fault_on:
        frozen.fault("effective_date_day_month_swapped")
    sub = demo_seed.subscription(start_date="2026-05-01")
    draft = demo_seed.draft(sub["id"], "change", changes={"start_date": "2026-04-03"})
    assert demo_seed.confirm_draft(draft["id"])["start_date"] == expected


@pytest.mark.parametrize("fault_on", [False, True])
def test_leap_day_fault(demo_sut, demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("date_parser_rejects_leap_day")
    body = {**_body(demo_seed), "start_date": "2028-02-29"}
    response = demo_sut.client.post("/api/subscriptions", body)
    assert (response.status == 422) is fault_on
    if not fault_on:
        assert response.json()["term_end"] == "2029-02-28"


@pytest.mark.parametrize("fault_on", [False, True])
def test_index_truncation_fault(demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("index_percent_truncated")
    sub = demo_seed.subscription(start_date="2025-03-15", frequency="YEARLY")
    demo_seed.book_due(sub["id"])
    preview = demo_seed.draft(sub["id"], "renew", index_percent=2.5)["preview"]
    assert preview["index_percent"] == (2.0 if fault_on else 2.5)


@pytest.mark.parametrize("fault_on", [False, True])
def test_create_throttle_fault(demo_sut, demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("create_throttled_every_other")
    body = _body(demo_seed)
    statuses = [demo_sut.client.post("/api/subscriptions", body) for _ in range(4)]
    codes = [r.status for r in statuses]
    if fault_on:
        assert codes.count(429) == 2 and codes.count(201) == 2
        refused = next(r for r in statuses if r.status == 429)
        assert refused.headers["retry-after"] == "0"
    else:
        assert codes == [201] * 4
