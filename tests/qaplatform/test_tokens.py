"""API tokens: hash-only storage, expiry, revocation, scope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from qaplatform.auth import tokens
from qaplatform.errors import BadRequest, Unauthorized
from tests.qaplatform._helpers import member

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _ci_member(store):
    member(store, "ci", "default", "tester")


def test_raw_token_is_never_stored(store):
    raw, row = tokens.issue(store, user_id="ci", project_id="default", role="tester", now=NOW)
    assert raw.startswith("qap_") and len(raw) > 40
    assert "token_hash" not in row
    dump = "\n".join(store.conn.iterdump())
    assert raw not in dump
    assert tokens.hash_token(raw) in dump


def test_verify_resolves_the_principal(store):
    raw, row = tokens.issue(store, user_id="ci", project_id="default", role="tester", now=NOW)
    p = tokens.verify(store, raw, now=NOW + timedelta(days=1))
    assert (p.user_id, p.project_id, p.role, p.token_id) == ("ci", "default", "tester", row["id"])
    assert store.token(row["id"])["last_used_at"] is not None


def test_expired_revoked_unknown_and_missing_tokens_are_refused(store):
    raw, row = tokens.issue(
        store, user_id="ci", project_id="default", role="tester", ttl_days=1, now=NOW
    )
    with pytest.raises(Unauthorized):
        tokens.verify(store, raw, now=NOW + timedelta(days=2))
    raw2, row2 = tokens.issue(store, user_id="ci", project_id="default", role="tester", now=NOW)
    assert store.revoke_token(row2["id"], "2026-01-05T11:00:00Z")
    assert not store.revoke_token(row2["id"], "2026-01-05T11:00:00Z")
    with pytest.raises(Unauthorized):
        tokens.verify(store, raw2, now=NOW)
    for bad in (None, "", "qap_unknown", "Bearer x", raw[:-1]):
        with pytest.raises(Unauthorized):
            tokens.verify(store, bad, now=NOW)


def test_issue_rules(store):
    with pytest.raises(BadRequest):
        tokens.issue(store, user_id="u", project_id="default", role="owner")
    with pytest.raises(BadRequest):
        tokens.issue(store, user_id="u", project_id="default", role="viewer", ttl_days=0)
    with pytest.raises(BadRequest):
        tokens.issue(store, user_id="u", project_id=None, role="viewer")


def test_bearer_header_parsing():
    assert tokens.bearer({"authorization": "Bearer qap_x"}) == "qap_x"
    assert tokens.bearer({"Authorization": "bearer  qap_y "}) == "qap_y"
    assert tokens.bearer({"authorization": "Basic abc"}) is None
    assert tokens.bearer({}) is None


def test_missing_token_answers_401_with_challenge(api):
    resp = api("GET", "/me", token=None)
    assert resp.status == 401
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")
    assert resp.payload["type"].endswith("/unauthorized")


def test_token_for_one_project_cannot_see_another(service, store, api):
    store.results.ensure_project("other", "Other")
    member(store, "ci", "other", "tester")
    raw, _ = tokens.issue(store, user_id="ci", project_id="other", role="tester")
    resp = api("GET", "/projects/default/runs", token=raw)
    assert resp.status == 404
    assert api("GET", "/projects/other/runs", token=raw).status == 200
