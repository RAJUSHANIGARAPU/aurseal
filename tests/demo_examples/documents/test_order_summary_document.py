"""The order summary's first amount matches the price calculation the customer was shown.

Price form (``POST /api/quotes/calculate``) -> capture the response -> submit the same values
-> exactly one order summary -> its first money amount equals a numeric field of the
captured response within 0.02. The request values are chosen so the response's numeric
fields are all distinct, which keeps "matches any field" from passing by coincidence.
"""

from __future__ import annotations

from decimal import Decimal

from oracles.pdf_text import extract_text
from tests.demo_examples.documents import oracles
from tests.demo_examples.documents._support import FROZEN_NOW, START_DATE

PRICE_FORM = {"net_amount": 28.4, "tax_rate": 20, "commission_percentage": 7}
TOLERANCE = Decimal("0.02")


def test_order_summary_total_matches_price_response(demo_control, demo_seed, demo_http, documents):
    demo_control.freeze(FROZEN_NOW)
    price = demo_http.post("quotes/calculate", json=PRICE_FORM).raise_for_status().json()
    sub = demo_seed.subscription(start_date=START_DATE, state="draft")
    demo_http.post(f"subscriptions/{sub['id']}/actions/submit", json=PRICE_FORM).raise_for_status()

    summaries = [d for d in documents.documents_of(sub["id"]) if d["template"] == "order_summary"]
    assert len(summaries) == 1, f"expected one order summary, got {summaries}"
    text = extract_text(documents.document_pdf(summaries[0]["id"]))
    printed = oracles.money_amounts(text)
    assert printed, f"no money amount on the order summary: {text}"
    path = oracles.matching_leaf(price, printed[0], TOLERANCE)
    assert path == "gross_amount", f"matched {path}; the first amount should be the gross"
