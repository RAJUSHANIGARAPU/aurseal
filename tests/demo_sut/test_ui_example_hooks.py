"""Demo additions made for the UI examples: picker keyboard and row picking, and direct
customer-number entry on the wizard."""

from __future__ import annotations


def test_wizard_offers_direct_customer_number_entry(demo_sut):
    html = demo_sut.client.get("/ui/subscriptions/new").body.decode()
    for test_id in ("customer-number", "customer-number-lookup", "customer-number-status"):
        assert f'data-testid="{test_id}"' in html
    assert "showCustomer" in html


def test_picker_searches_on_enter_and_picks_on_row_click(demo_sut):
    html = demo_sut.client.get("/ui/subscriptions/new").body.decode()
    assert "ev.key === 'Enter'" in html and "picker-choose" in html
    assert "closest('[data-testid=\"picker-row\"]')" in html


def test_select_reset_fault(demo_sut, demo_seed, demo_control):
    """The closure screen carries the switch to its script; the healthy page keeps the choice."""
    sub = demo_seed.subscription()
    draft = demo_seed.draft(sub["id"], "cancel")
    healthy = demo_sut.client.get(f"/ui/drafts/{draft['id']}").body.decode()
    assert '"reset_select_on_error": false' in healthy
    demo_control.fault("form_resets_select_after_error")
    faulty = demo_sut.client.get(f"/ui/drafts/{draft['id']}").body.decode()
    assert '"reset_select_on_error": true' in faulty
    assert "reason.options[0].value : picked" in faulty
