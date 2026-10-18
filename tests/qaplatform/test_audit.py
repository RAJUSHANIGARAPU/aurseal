"""The audit log: one row per change, in the same transaction, hash-chained per project."""

from __future__ import annotations

import inspect
import json
import sqlite3

import pytest

from qaplatform import audit
from qaplatform.service import PlatformService
from tests.qaplatform._helpers import finish, new_run, push

SECRET_MESSAGE = "card number 4111 1111 1111 1111 in the failure text"


def _chain(store, project="default"):
    return store.scoped(project).audit_chain()


def _seed(api, n_runs=2):
    for i in range(n_runs):
        run_id = new_run(api, external_id=f"ci-{i}")
        push(api, run_id, [{"test_id": "t::a", "outcome": "failed", "message": SECRET_MESSAGE}])
        finish(api, run_id)


def test_every_change_is_one_row_and_the_chain_verifies(api, store):
    _seed(api)
    rows = _chain(store)
    assert [r["action"] for r in rows] == ["run.create", "results.append", "run.finish"] * 2
    assert {r["actor_user_id"] for r in rows} == {"admin"}
    assert all(r["token_id"].startswith("tok_") for r in rows)
    assert all(r["request_id"].startswith("req_") for r in rows)
    report = audit.verify_chain(store.scoped("default"))
    assert report.ok and report.rows_checked == 6 and report.head == rows[-1]["row_hash"]


def test_replays_that_change_nothing_are_not_audited(api, store):
    body = {"schema_version": "1.0", "name": "n", "external_id": "same"}
    api("POST", "/projects/default/runs", body)
    api("POST", "/projects/default/runs", body)
    assert [r["action"] for r in _chain(store)] == ["run.create"]


def test_result_payloads_never_reach_the_diff(api, store):
    _seed(api, 1)
    dump = json.dumps(_chain(store))
    assert "4111" not in dump
    batch = next(r for r in _chain(store) if r["action"] == "results.append")
    assert json.loads(batch["diff_json"]) == {"added": 1, "received": 1}


def test_updates_and_deletes_are_refused_by_the_database(api, store):
    _seed(api, 1)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("UPDATE qap_audit_log SET action = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("DELETE FROM qap_audit_log")


def _tamper(store, sql, params=()):
    """Someone with write access drops the guard first; the chain must still notice."""
    store.conn.execute("DROP TRIGGER qap_audit_log_no_update")
    store.conn.execute("DROP TRIGGER qap_audit_log_no_delete")
    store.conn.execute(sql, params)


def test_edited_row_is_named(api, store):
    _seed(api)
    target = _chain(store)[2]["seq"]
    _tamper(store, "UPDATE qap_audit_log SET diff_json = '{}' WHERE seq = ?", (target,))
    report = audit.verify_chain(store.scoped("default"))
    assert (report.ok, report.first_bad_seq) == (False, target)
    assert "changed" in report.reason


def test_deleted_middle_row_is_named(api, store):
    _seed(api)
    rows = _chain(store)
    _tamper(store, "DELETE FROM qap_audit_log WHERE seq = ?", (rows[2]["seq"],))
    report = audit.verify_chain(store.scoped("default"))
    assert (report.ok, report.first_bad_seq) == (False, rows[3]["seq"])
    assert "removed or reordered" in report.reason


def test_reordered_rows_are_named(api, store):
    _seed(api)
    a, b = _chain(store)[1], _chain(store)[2]
    _tamper(store, "UPDATE qap_audit_log SET seq = -1 WHERE seq = ?", (a["seq"],))
    store.conn.execute("UPDATE qap_audit_log SET seq = ? WHERE seq = ?", (a["seq"], b["seq"]))
    store.conn.execute("UPDATE qap_audit_log SET seq = ? WHERE seq = -1", (b["seq"],))
    report = audit.verify_chain(store.scoped("default"))
    assert report.ok is False and report.first_bad_seq == a["seq"]


def test_chains_are_per_project(api, store):
    store.results.ensure_project("other", "Other")
    new_run(api)
    new_run(api, project="other")
    assert audit.verify_chain(store.scoped("default")).ok
    assert audit.verify_chain(store.scoped("other")).ok
    assert _chain(store, "other")[0]["prev_hash"] == audit.GENESIS


def test_a_failing_change_keeps_neither_change_nor_row(service, admin, store, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(audit, "record", broken)
    with pytest.raises(RuntimeError):
        service.create_run(admin, "default", {"schema_version": "1.0", "name": "n"})
    assert store.scoped("default").runs() == []
    assert _chain(store) == []


def _mutating_methods() -> list[str]:
    return sorted(
        name
        for name, fn in inspect.getmembers(PlatformService, inspect.isfunction)
        if not name.startswith("_") and "self._write(" in inspect.getsource(fn)
    )


def test_every_mutating_method_writes_exactly_one_row(api, service, admin, store):
    calls = {
        "create_run": lambda: service.create_run(
            admin, "default", {"schema_version": "1.0", "name": "n"}
        ),
        "append_results": lambda: service.append_results(
            admin,
            "default",
            run_id,
            {"schema_version": "1.0", "results": [{"test_id": "x", "outcome": "passed"}]},
        ),
        "append_junit": lambda: service.append_junit(
            admin, "default", run_id, b'<testsuite><testcase classname="a" name="b"/></testsuite>'
        ),
        "append_verdicts": lambda: service.append_verdicts(
            admin,
            "default",
            run_id,
            [{"test_id": "x", "kind": "coverage", "subject": "REQ-1", "verdict": "recorded"}],
        ),
        "add_evidence": lambda: service.add_evidence(
            admin,
            "default",
            run_id,
            {"schema_version": "1.0", "links": [{"kind": "log", "uri": "a.log"}]},
        ),
        "finish_run": lambda: service.finish_run(
            admin, "default", run_id, {"schema_version": "1.0"}
        ),
        "request_run": lambda: service.request_run(
            admin, "default", {"schema_version": "1.0", "target": "smoke"}
        ),
        "grant_member": lambda: service.grant_member(
            admin, "default", {"schema_version": "1.0", "user_id": "dev", "role": "tester"}
        ),
        "issue_token": lambda: issued.append(
            service.issue_token(
                admin, "default", {"schema_version": "1.0", "user_id": "dev", "role": "viewer"}
            )
        ),
        "revoke_token": lambda: service.revoke_token(admin, "default", issued[0]["id"]),
        "remove_member": lambda: service.remove_member(admin, "default", "dev"),
        "update_project": lambda: service.update_project(
            admin, "default", {"schema_version": "1.0", "name": "Renamed"}
        ),
        "start_manual_run": lambda: manual_runs.append(
            service.start_manual_run(
                admin,
                "default",
                {
                    "schema_version": "1.0",
                    "name": "m",
                    "cases": [{"key": "manual::login", "steps": ["open the page"]}],
                },
            )
        ),
        "record_manual_step": lambda: service.record_manual_step(
            admin,
            "default",
            manual_runs[0]["executions"][0]["id"],
            1,
            {"schema_version": "1.0", "status": "pass"},
        ),
        "finish_manual_execution": lambda: service.finish_manual_execution(
            admin, "default", manual_runs[0]["executions"][0]["id"]
        ),
        "start_session": lambda: sessions.append(
            service.start_session(
                admin, "default", {"schema_version": "1.0", "charter": "explore checkout"}
            )
        ),
        "add_session_note": lambda: service.add_session_note(
            admin,
            "default",
            sessions[0]["id"],
            {"schema_version": "1.0", "kind": "idea", "text": "try a long name"},
        ),
        "end_session": lambda: service.end_session(
            admin, "default", sessions[0]["id"], {"schema_version": "1.0", "debrief": "done"}
        ),
    }
    manual_runs: list[dict] = []
    sessions: list[dict] = []
    issued: list[dict] = []
    run_id = new_run(api)
    assert set(_mutating_methods()) <= set(calls), "a new mutating method needs a case here"
    order = [
        "create_run",
        "append_results",
        "append_junit",
        "append_verdicts",
        "add_evidence",
        "finish_run",
        "request_run",
        "grant_member",
        "issue_token",
        "revoke_token",
        "remove_member",
        "update_project",
        "start_manual_run",
        "record_manual_step",
        "finish_manual_execution",
        "start_session",
        "add_session_note",
        "end_session",
    ]
    assert set(order) == set(calls)
    for name in order:
        before = len(_chain(store))
        calls[name]()
        assert len(_chain(store)) == before + 1, name


def test_audit_endpoints(api):
    _seed(api, 1)
    page = api("GET", "/projects/default/audit", query={"limit": "2"}).payload
    assert [e["action"] for e in page["entries"]] == ["run.create", "results.append"]
    rest = api("GET", "/projects/default/audit", query={"cursor": page["next_cursor"]}).payload[
        "entries"
    ]
    assert [e["action"] for e in rest] == ["run.finish"]
    only = api("GET", "/projects/default/audit", query={"action": "run.finish"}).payload
    assert len(only["entries"]) == 1 and only["entries"][0]["diff"]["status"] == "finished"
    verify = api("GET", "/projects/default/audit/verify").payload
    head = api("GET", "/projects/default/audit/head").payload
    assert verify["ok"] and head["head"] == verify["head"] and head["seq"] == 3


def test_audit_cli(db_path, store, api, capsys, tmp_path):
    from qaplatform.cli import main

    _seed(api, 1)
    assert main(["audit", "verify", "--db", db_path]) == 0
    assert json.loads(capsys.readouterr().out)["rows_checked"] == 3
    out = tmp_path / "audit.jsonl"
    assert main(["audit", "export", "--db", db_path, "--out", str(out)]) == 0
    assert len(out.read_text().splitlines()) == 3
    _tamper(store, "UPDATE qap_audit_log SET action = 'x' WHERE seq = 1")
    capsys.readouterr()
    assert main(["audit", "verify", "--db", db_path]) == 1
