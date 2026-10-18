"""The plan update notice: rendered and downloadable, and it states the booked pro-rata."""

from __future__ import annotations

from decimal import Decimal

from oracles import ledger_checks
from oracles.pdf_text import extract_text
from oracles.pdf_text_view import is_pdf
from tests.demo_examples.documents import oracles
from tests.demo_examples.documents._support import (
    BOOK_UNTIL,
    CHANGE_EFFECTIVE,
    FIXTURES,
    FROZEN_NOW,
    REPORTS_DIR,
    START_DATE,
    load_json,
    plus_days,
    seed_content,
)

TOLERANCE = Decimal("0.02")


def test_change_document_rendered_and_downloadable(demo_control, demo_seed, documents):
    request = load_json(FIXTURES / "change-request.json")
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    effective = plus_days(FROZEN_NOW[:10], request["effective_in_days"])
    draft = demo_seed.draft(
        sub["id"], request["kind"], effective_date=effective, changes=request["changes"]
    )
    changed = demo_seed.confirm_draft(draft["id"])

    change_docs = [
        d
        for d in documents.documents_of(sub["id"])
        if d["template"] == "plan_update_notice" and d["version"] == changed["version"]
    ]
    assert len(change_docs) == 1 and str(change_docs[0]["id"]).strip(), change_docs
    pdf = documents.document_pdf(change_docs[0]["id"])
    assert is_pdf(pdf)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    saved = REPORTS_DIR / f"{sub['number']}-plan-update-v{changed['version']}.pdf"
    saved.write_bytes(pdf)
    assert saved.stat().st_size == len(pdf) > 0


def test_change_document_states_the_pro_rata_adjustment(demo_control, demo_seed, documents):
    content = seed_content()
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE, data_region="EU-CENTRAL")
    demo_seed.book_due(sub["id"], until=BOOK_UNTIL)
    old_net = Decimal(str(sub["price"]["monthly_net"]))
    changes = {**content["change"], "monthly_net": float(old_net + Decimal("6.00"))}
    draft = demo_seed.draft(sub["id"], "change", effective_date=CHANGE_EFFECTIVE, changes=changes)
    changed = demo_seed.confirm_draft(draft["id"])
    demo_seed.book_due(sub["id"], until=BOOK_UNTIL)

    legs = [t for t in demo_seed.transactions(sub["id"]) if t["kind"] == "CHANGE"]
    assert len(legs) == 1 and legs[0]["state"] == "BOOKED", legs
    leg = legs[0]
    assert (
        ledger_checks.additive_identity([leg], ["net", "tax", "fee_net", "fee_tax"], "total") == []
    )

    doc = next(
        d
        for d in documents.documents_of(sub["id"])
        if d["template"] == "plan_update_notice" and d["version"] == changed["version"]
    )
    text = extract_text(documents.document_pdf(doc["id"]))
    # anchor: the notice is about the period that starts on the effective date
    assert f"Applies from: {CHANGE_EFFECTIVE}" in text, text
    assert leg["period_start"] == CHANGE_EFFECTIVE

    adjustment = oracles.money_after_label(text, "Adjustment:")
    assert adjustment is not None, text
    assert abs(adjustment - Decimal(str(leg["total"]))) <= TOLERANCE, (
        f"the notice states {adjustment}, the booked change leg is {leg['total']}"
    )
    invoice = next(
        i
        for i in documents.engine.get(f"subscriptions/{sub['id']}/invoices").json()
        if i["transaction_id"] == leg["id"]
    )
    assert abs(Decimal(str(invoice["total"])) - adjustment) <= TOLERANCE
    # a full-period figure on the adjustment line would be the wrong number
    full_term = {k: changed["price"][k] for k in ("period_gross", "period_net", "monthly_gross")}
    assert all(abs(Decimal(str(v)) - adjustment) > TOLERANCE for v in full_term.values()), (
        f"adjustment {adjustment} equals a full-period figure {full_term}"
    )
    oracles.assert_line_value(text, "Data region:", changed["data_region"])
