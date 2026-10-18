"""Managed ledger accounts refuse a rename; unmanaged ones are renamed (and restored)."""

from __future__ import annotations

MANAGED_MESSAGE = "Account is managed by the system and cannot be renamed."


def test_managed_account_rename_refused_unmanaged_allowed(billing):
    accounts = billing.accounts()
    configured = set(billing.ledger_codes().values())
    managed = [a for a in accounts if a["number"] in configured]
    assert managed and all(a["managed"] is True for a in managed)
    for account in managed:
        refused = billing.rename_account(account["id"], "Renamed by a test")
        assert refused.status == 409, refused
        assert refused.json()["message"] == MANAGED_MESSAGE
    names = {a["id"]: a["name"] for a in billing.accounts()}
    assert all(names[a["id"]] == a["name"] for a in managed), "a refused rename changed a name"

    unmanaged = next(a for a in accounts if a["managed"] is False)
    try:
        renamed = billing.rename_account(unmanaged["id"], f"{unmanaged['name']} (test)")
        assert renamed.status == 200, renamed
        assert renamed.json()["name"] == f"{unmanaged['name']} (test)"
    finally:
        restored = billing.rename_account(unmanaged["id"], unmanaged["name"])
    assert restored.status == 200 and restored.json()["name"] == unmanaged["name"]
