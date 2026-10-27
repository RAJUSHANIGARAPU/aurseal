"""Tests for verification.destructive.delete_contract against an in-memory store."""

from __future__ import annotations

import pytest

from api.clients.http_engine import ApiResponse


class Store:
    """A delete endpoint with switchable defects."""

    def __init__(self, *, anon_deletes=False, repeat_status=404, permitted=True, real=204):
        self.items = {"item-1"}
        self.anon_deletes = anon_deletes
        self.repeat_status = repeat_status
        self.permitted = permitted
        self.real = real
        self.deleted: set[str] = set()

    def delete(self, ident: str) -> ApiResponse:
        if not self.permitted:
            return ApiResponse(403, b'{"detail": "missing permission items.delete"}')
        if ident in self.items:
            if self.real < 300:
                self.items.discard(ident)
                self.deleted.add(ident)
            return ApiResponse(self.real)
        if ident in self.deleted:
            return ApiResponse(self.repeat_status)
        return ApiResponse(404)

    def delete_anon(self, ident: str) -> ApiResponse:
        if self.anon_deletes:
            self.items.discard(ident)
            return ApiResponse(204)
        return ApiResponse(401)

    def exists(self, ident: str) -> bool:
        return ident in self.items


def _run(store: Store, **kw):
    from verification.destructive import delete_contract

    return delete_contract(
        store.delete,
        store.delete_anon,
        store.exists,
        item_id="item-1",
        impossible_id="does-not-exist-0000",
        **kw,
    )


def test_a_correct_endpoint_has_no_findings():
    store = Store()
    assert _run(store) == []
    assert not store.exists("item-1")


def test_anonymous_delete_that_removes_the_item_is_reported_and_stops():
    findings = _run(Store(anon_deletes=True))
    assert any("anonymous delete" in f for f in findings)
    assert any("no longer exists" in f for f in findings)


def test_repeat_delete_that_succeeds_is_a_finding():
    findings = _run(Store(repeat_status=204))
    assert len(findings) == 1 and findings[0].startswith("repeat delete of item-1 answered")


def test_missing_permission_is_no_verdict_not_a_pass():
    with pytest.raises(pytest.skip.Exception, match="caller lacks permission"):
        _run(Store(permitted=False), permission_hint="permission")


def test_a_failed_real_delete_is_reported():
    findings = _run(Store(real=500))
    assert findings and "expected 2xx" in findings[-1]


def test_ownership_guard_refusal_abstains_before_deleting():
    store = Store()
    with pytest.raises(pytest.skip.Exception, match="ownership guard refused"):
        _run(store, guard=lambda: "2 items carry key 'k'")
    assert store.exists("item-1"), "a refused guard must leave the item alone"


def test_ids_must_differ():
    from verification.destructive import delete_contract

    with pytest.raises(ValueError):
        delete_contract(
            lambda i: ApiResponse(404),
            lambda i: ApiResponse(401),
            lambda i: True,
            item_id="a",
            impossible_id="a",
        )
