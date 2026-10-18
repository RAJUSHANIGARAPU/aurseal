"""Ledger accounts: Edit is disabled exactly for the rows the API calls managed."""

from __future__ import annotations

import pytest

from ui.pages.demo.admin import AccountsScreen
from verification.evidence_verdict import EvidenceVerdict

pytestmark = pytest.mark.demo_ui


def test_edit_disabled_exactly_for_managed_rows(demo_page, demo_sut, demo_control):
    api = {a["id"]: a for a in demo_sut.client.ok("GET", "/api/accounts")}
    screen = AccountsScreen(demo_page, demo_sut.base_url).open()
    rows = screen.rows()
    assert rows and {r.id for r in rows} == set(api)
    mismatches = [
        (r.number, r.edit_enabled, api[r.id]["managed"])
        for r in rows
        if r.edit_enabled == bool(api[r.id]["managed"])
    ]
    assert mismatches == [], f"(number, edit enabled, managed) disagree: {mismatches}"
    assert any(r.edit_enabled for r in rows) and any(not r.edit_enabled for r in rows)
    assert all(r.managed_marker == bool(api[r.id]["managed"]) for r in rows)

    editable = next(r for r in rows if r.edit_enabled)
    edit = screen.open_edit(editable.id)
    assert edit.number() == editable.number and edit.number_read_only() is True
    assert edit.name() == api[editable.id]["name"] and edit.name_read_only() is False
    EvidenceVerdict("account edit menu follows the managed flag").proved(
        f"{len(rows)} rows, {sum(r.edit_enabled for r in rows)} editable, all match the API"
    )
