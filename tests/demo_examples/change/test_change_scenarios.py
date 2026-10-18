"""Plan changes against demo_sut: one generic test per manifest case, plus the cases that need
more than "set a field, read it back".

The generic test seeds the same baseline for every case, checks first that each expected
value really differs from the baseline (otherwise the case could pass without changing
anything), applies the case's changes through a draft, and then checks the end state, every
expected value and exactly one new CHANGE transaction.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from demo_sut.customers import make_iban
from test_data.scenario_manifest import CaseManifest
from tests.demo_examples.change.support import (
    active_subscription,
    change,
    change_transactions,
    current,
    finish_change,
    open_change,
    read_path,
)

MANIFEST = CaseManifest.load(Path(__file__).with_name("change-cases.json"))
GENERIC = [
    p
    for p in MANIFEST.params()
    if p.values[0].test and p.values[0].test.endswith("::test_change_case")
]

BASELINE = {
    "spend_cap": 0,
    "addons": {"sso": False, "audit_trail": False, "sandbox": False},
    "data_region": "EU-CENTRAL",
    "workspace_type": "TEAM_SPACE",
    "seat_blocks": [{"name": "Core team", "seats": 8}],
    "payment_method": {"type": "CARD"},
    "invoice_delivery": "EMAIL",
    "billing_contact": {"name": "Kit Example", "email": "kit@example.test"},
}
NOW, START = "2026-07-14", "2026-06-01"


def baseline_subscription(seed, control) -> dict:
    sub = active_subscription(seed, control, now=NOW, start=START, **BASELINE)
    assert not sub["invoice_notes"] and not sub["labels"] and sub["po_reference"] is None
    return sub


@pytest.mark.parametrize("case", GENERIC)
def test_change_case(case, demo_seed, demo_control):
    inputs = case.inputs
    sub = baseline_subscription(demo_seed, demo_control)
    before_changes = len(change_transactions(demo_seed, sub["id"]))
    for path, target in inputs["then"].items():
        assert read_path(sub, path) != target, f"{case.id}: {path} already equals {target!r}"

    view = change(demo_seed, sub["id"], inputs["apply"])

    assert view["status"] == inputs["ends_in"], f"{case.id}: ended in {view['status']}"
    for path, target in inputs["then"].items():
        assert read_path(view, path) == target, f"{case.id}: {path} = {read_path(view, path)!r}"
    assert len(change_transactions(demo_seed, sub["id"])) == before_changes + 1


# -- cases with more to check ------------------------------------------------------------------


def test_spend_cap_change_price_unchanged(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    view = change(demo_seed, sub["id"], {"spend_cap": 400})
    assert view["spend_cap"] == 400.0 and view["price"] == sub["price"]
    (row,) = change_transactions(demo_seed, sub["id"])
    assert row["net"] == 0, "a spend cap has no price effect"


def test_addons_flip_one_at_a_time(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    for name in ("sso", "audit_trail", "sandbox"):
        view = change(demo_seed, sub["id"], {"addons": {name: True}})
        assert view["addons"][name] is True
        others = {k: v for k, v in view["addons"].items() if k != name}
        flipped_before = {"sso": ["sso"], "audit_trail": ["sso"], "sandbox": ["sso", "audit_trail"]}
        assert {k for k, v in others.items() if v} == set(flipped_before[name]) - {name}
    assert current(demo_seed, sub["id"])["price"] == sub["price"]


def test_data_region_move(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    for region in ("EU-NORTH", "AP-SOUTH", "EU-CENTRAL"):
        assert change(demo_seed, sub["id"], {"data_region": region})["data_region"] == region
    refused = demo_seed.client.post(
        f"/api/subscriptions/{sub['id']}/drafts",
        {"kind": "change", "changes": {"data_region": "MOON-BASE"}},
    )
    assert refused.status == 422 and "data_region" in refused.json()["fields"]


def test_workspace_type_change(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    view = change(demo_seed, sub["id"], {"workspace_type": "SANDBOX"})
    assert view["workspace_type"] == "SANDBOX"
    cleared = change(demo_seed, sub["id"], {"workspace_type": None})
    assert cleared["workspace_type"] is None, "the workspace type is optional"


def test_amend_invoice_note_across_two_changes(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    first = change(demo_seed, sub["id"], {"add_invoice_notes": [{"text": "Net 14 days."}]})
    (note,) = first["invoice_notes"]
    second = change(
        demo_seed,
        sub["id"],
        {"amend_invoice_notes": [{"number": note["number"], "text": "Net 21 days."}]},
    )
    assert second["invoice_notes"] == [
        {"number": note["number"], "version": 2, "text": "Net 21 days."}
    ]
    old = current(demo_seed, sub["id"], version=first["version"])
    assert old["invoice_notes"][0]["text"] == "Net 14 days.", "the earlier version keeps its text"


def test_add_label(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    view = change(demo_seed, sub["id"], {"add_labels": ["L-PILOT", "L-INTERNAL"]})
    assert [(lbl["code"], lbl["title"]) for lbl in view["labels"]] == [
        ("L-PILOT", "Pilot"),
        ("L-INTERNAL", "Internal use"),
    ]


def test_price_only_change_leaves_other_fields(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    view = change(demo_seed, sub["id"], {"monthly_net": 41.25})
    assert view["price"]["monthly_net"] == 41.25
    for key in BASELINE:
        assert view[key] == sub[key], f"{key} moved on a price-only change"


def test_create_process_change_lifecycle(demo_seed, demo_control):
    """Create, book, raise the price mid-term: 37.50 net is 45.00 gross at 20 % tax."""
    sub = active_subscription(demo_seed, demo_control, now=NOW, start=START)
    assert sub["status"] == "active"
    view = change(demo_seed, sub["id"], {"monthly_net": 37.5})
    assert view["price"]["monthly_net"] == 37.5 and view["price"]["monthly_gross"] == 45.0
    (row,) = change_transactions(demo_seed, sub["id"])
    assert row["kind"] == "CHANGE" and Decimal(str(row["net"])) >= 0


def test_direct_debit_account_verification(demo_seed, demo_control):
    """Switching to direct debit runs the name check; a first NO_MATCH is recorded, not hidden."""
    account = make_iban(bban="CHGDEBIT000033")
    demo_control.settings(account_check_mismatch_once=[account])
    sub = baseline_subscription(demo_seed, demo_control)
    view = change(
        demo_seed, sub["id"], {"payment_method": {"type": "DIRECT_DEBIT", "account": account}}
    )
    assert view["payment_method"] == {"type": "DIRECT_DEBIT", "account": account}
    assert view["account_check"]["result"] == "NO_MATCH"
    again = change(
        demo_seed, sub["id"], {"payment_method": {"type": "DIRECT_DEBIT", "account": account}}
    )
    assert again["account_check"]["result"] == "MATCH", "the stub answers NO_MATCH only once"
    missing = demo_seed.client.post(
        f"/api/subscriptions/{sub['id']}/drafts",
        {"kind": "change", "changes": {"payment_method": {"type": "DIRECT_DEBIT"}}},
    )
    assert missing.status == 422 and "payment_method.account" in missing.json()["fields"]


def test_seat_blocks_change_persists(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    blocks = [{"name": "Core team", "seats": 10}, {"name": "Support desk", "seats": 4}]
    view = change(demo_seed, sub["id"], {"seat_blocks": blocks})
    assert view["seat_blocks"] == blocks and view["seat_total"] == 14
    assert current(demo_seed, sub["id"])["seat_total"] == 14


def test_ownership_transfer_needs_acceptance(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    successor = demo_seed.customer(display_name="Lantern Row Successor")
    draft = open_change(demo_seed, sub["id"], {"customer_id": successor["id"]})
    assert draft["steps"] == ["details", "price", "ownership_acceptance"]
    refused = demo_seed.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert refused.status == 422
    demo_seed.client.ok("POST", f"/api/drafts/{draft['id']}/actions/accept-ownership")
    view = finish_change(demo_seed, draft)
    assert view["holder"]["id"] == successor["id"]


def test_billing_contact_verbatim_price_unchanged(demo_seed, demo_control):
    sub = baseline_subscription(demo_seed, demo_control)
    contact = {"name": "  Dee O'Neill-Šimek  ", "email": "dee@example.test"}
    view = change(demo_seed, sub["id"], {"billing_contact": contact})
    assert view["billing_contact"] == contact, "the contact is stored exactly as typed"
    assert view["price"] == sub["price"]
