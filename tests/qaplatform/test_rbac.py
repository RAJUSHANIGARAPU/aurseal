"""RBAC: the matrix as data, enforcement in the service only, bindings that bite."""

from __future__ import annotations

import inspect
import re
import shutil
import sqlite3

import pytest

from qaplatform import rbac
from qaplatform.auth import tokens
from qaplatform.auth.tokens import Principal
from qaplatform.errors import BadRequest, Conflict, Forbidden, Unauthorized
from qaplatform.service import PlatformService
from tests.qaplatform._helpers import Api, member, new_run, token_for

# The whole table, spelled out: widening a role is a visible change to this literal.
EXPECTED = {
    "viewer": {"read"},
    "tester": {"read", "push_results", "record_manual"},
    "lead": {"read", "push_results", "record_manual", "trigger_run", "read_audit"},
    "admin": {
        "read",
        "push_results",
        "record_manual",
        "trigger_run",
        "read_audit",
        "manage_tokens",
        "manage_members",
        "manage_project",
    },
}


def test_matrix_is_exactly_the_documented_table():
    assert {r.value: {p.value for p in perms} for r, perms in rbac.MATRIX.items()} == EXPECTED


def test_roles_are_ordered_and_lower_picks_the_weaker():
    assert [r.value for r in rbac.ORDER] == ["viewer", "tester", "lead", "admin"]
    assert rbac.lower("admin", "tester") == "tester"
    assert rbac.lower("viewer", "lead") == "viewer"
    assert rbac.lowest_role_with("trigger_run") is rbac.Role.LEAD


# ---------------------------------------------------------------- service enforcement

_GUARD = re.compile(r"self\._(?:scope|write)\(\s*principal,\s*project,\s*([A-Z_]+)")
# Not project-scoped (instance level, or delegating to a guarded method); each is covered
# by its own test: create_project / list_projects in test_tenancy.py.
_EXEMPT = {
    "authenticate",
    "health",
    "ready",
    "me",
    "idempotent",
    "requirement",
    "create_project",
    "list_projects",
}


def _guarded() -> dict[str, str]:
    """Public service methods -> the permission constant their guard names."""
    import qaplatform.service as mod

    out = {}
    for name, fn in inspect.getmembers(PlatformService, inspect.isfunction):
        if name.startswith("_") or name in _EXEMPT:
            continue
        match = _GUARD.search(inspect.getsource(fn))
        assert match, f"PlatformService.{name} does not pass through _scope/_write"
        out[name] = getattr(mod, match.group(1)).value
    return out


def test_every_public_method_names_its_permission():
    guarded = _guarded()
    assert guarded["create_run"] == "push_results"
    assert guarded["request_run"] == "trigger_run"
    assert guarded["audit_log"] == "read_audit"
    assert guarded["issue_token"] == "manage_tokens"
    assert guarded["get_run"] == "read"


def _weakest_without(permission: str) -> str | None:
    lacking = [r for r in rbac.ORDER if rbac.Permission(permission) not in rbac.MATRIX[r]]
    return lacking[-1].value if lacking else None


def test_every_method_refuses_a_role_without_its_permission(service):
    """Called with the strongest role that lacks the permission and junk arguments, every
    method refuses on the role, before it looks at the arguments."""
    for name, permission in _guarded().items():
        role = _weakest_without(permission)
        if role is None:
            continue
        p = Principal("u", "tok_x", role, "default", "")
        fn = getattr(service, name)
        positional = [
            q
            for q in inspect.signature(fn).parameters.values()
            if q.kind is q.POSITIONAL_OR_KEYWORD
        ]
        args = [p, "default"] + [{} if q.name == "body" else "x" for q in positional[2:]]
        with pytest.raises(Forbidden):
            fn(*args)


# ---------------------------------------------------------------- through the API


def test_roles_through_the_api(service, store):
    run_owner = Api(service, token_for(store, "lead1", "lead"))
    run_id = new_run(run_owner)
    viewer = Api(service, token_for(store, "v1", "viewer"))
    tester = Api(service, token_for(store, "t1", "tester"))
    batch = {"schema_version": "1.0", "results": [{"test_id": "a", "outcome": "passed"}]}
    assert viewer("GET", f"/projects/default/runs/{run_id}").status == 200
    assert viewer("POST", f"/projects/default/runs/{run_id}/results", batch).status == 403
    assert tester("POST", f"/projects/default/runs/{run_id}/results", batch).status == 200
    rr = {"schema_version": "1.0", "target": "smoke"}
    assert tester("POST", "/projects/default/run-requests", rr).status == 403
    assert run_owner("POST", "/projects/default/run-requests", rr).status == 202
    assert tester("GET", "/projects/default/audit").status == 403
    assert run_owner("GET", "/projects/default/audit").status == 200
    assert run_owner("GET", "/projects/default/tokens").status == 403
    me = tester("GET", "/me").payload
    assert me["role"] == "tester" and "push_results" in me["permissions"]


def test_admin_manages_members_and_tokens(api, store, service):
    grant = api(
        "POST",
        "/projects/default/members",
        {"schema_version": "1.0", "user_id": "ci-bot", "role": "tester"},
    )
    assert grant.status == 200
    issued = api(
        "POST",
        "/projects/default/tokens",
        {"schema_version": "1.0", "user_id": "ci-bot", "role": "tester", "label": "ci"},
    )
    assert issued.status == 201 and issued.payload["token"].startswith("qap_")
    raw = issued.payload["token"]
    listed = api("GET", "/projects/default/tokens").payload["tokens"]
    assert raw not in str(listed) and listed[0]["label"] == "ci"
    bot = Api(service, raw)
    assert bot("GET", "/me").status == 200
    assert api("DELETE", f"/projects/default/tokens/{issued.payload['id']}").status == 200
    assert bot("GET", "/me").status == 401
    audit = [e["action"] for e in api("GET", "/projects/default/audit").payload["entries"]]
    assert audit == ["member.grant", "token.issue", "token.revoke"]
    assert raw not in str(api("GET", "/projects/default/audit").payload)


def test_token_role_cannot_exceed_the_binding(store):
    member(store, "dev", "default", "tester")
    with pytest.raises(BadRequest):
        tokens.issue(store, user_id="dev", project_id="default", role="admin")
    with pytest.raises(BadRequest):
        tokens.issue(store, user_id="stranger", project_id="default", role="viewer")


def test_lowering_a_binding_lowers_existing_tokens_at_once(api, store, service):
    raw = token_for(store, "dev", "lead")
    dev = Api(service, raw)
    assert dev("GET", "/me").payload["role"] == "lead"
    api(
        "POST",
        "/projects/default/members",
        {"schema_version": "1.0", "user_id": "dev", "role": "viewer"},
    )
    assert dev("GET", "/me").payload["role"] == "viewer"


def test_removing_a_member_revokes_their_tokens(api, store, service):
    raw = token_for(store, "dev", "tester")
    resp = api("DELETE", "/projects/default/members/dev")
    assert resp.payload["tokens_revoked"] == 1
    assert Api(service, raw)("GET", "/me").status == 401


def test_last_admin_is_protected(service, store):
    admin_raw = token_for(store, "owner", "admin")
    owner = service.authenticate(admin_raw)
    with pytest.raises(Conflict):
        service.remove_member(owner, "default", "owner")
    with pytest.raises(Conflict):
        service.grant_member(
            owner, "default", {"schema_version": "1.0", "user_id": "owner", "role": "viewer"}
        )


def test_disabled_owner_cannot_use_a_token(store):
    raw = token_for(store, "dev", "tester")
    store.conn.execute("UPDATE qap_users SET disabled = 1 WHERE id = 'dev'")
    with pytest.raises(Unauthorized):
        tokens.verify(store, raw)


def test_cli_token_create_grants_and_audits(db_path, store, capsys):
    from qaplatform.cli import main

    assert main(["token", "create", "--db", db_path, "--user", "ci", "--role", "tester"]) == 0
    assert store.binding("ci", "default")["role"] == "tester"
    actions = [r["action"] for r in store.scoped("default").audit_chain()]
    assert actions == ["member.grant", "token.issue"]
    assert store.scoped("default").audit_chain()[0]["actor_user_id"] == "local-operator"


def test_migration_keeps_tokens_issued_before_rbac(tmp_path):
    """Tokens from before users and bindings existed stay valid after the upgrade."""
    from qaplatform import store as qstore
    from results import migrations
    from results.store import SqliteResultsStore

    staged = tmp_path / "mig"
    staged.mkdir()
    for name in ("0001_api.sql", "0002_audit.sql"):
        shutil.copy(qstore.MIGRATIONS_DIR / name, staged / name)
    results = SqliteResultsStore(tmp_path / "old.db")
    migrations.register_domain(qstore.DOMAIN, staged, qstore.PREFIX)
    try:
        migrations.apply(results.conn, [qstore.DOMAIN])
    finally:
        migrations.unregister_domain(qstore.DOMAIN)
    raw = "qap_" + "x" * 43
    results.conn.execute(
        "INSERT INTO qap_tokens (id, project_id, user_id, role, token_hash, created_at,"
        " expires_at) VALUES ('tok_old', 'default', 'ci', 'tester', ?, '2026-01-01T00:00:00Z',"
        " '2999-01-01T00:00:00Z')",
        (tokens.hash_token(raw),),
    )
    results.close()
    upgraded = qstore.PlatformStore.open(str(tmp_path / "old.db"))
    p = tokens.verify(upgraded, raw)
    assert (p.user_id, p.role) == ("ci", "tester")
    upgraded.close()
    with pytest.raises(sqlite3.Error):
        sqlite3.connect(tmp_path / "old.db").execute("SELECT * FROM qap_nothing")
