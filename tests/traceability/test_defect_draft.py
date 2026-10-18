"""Drafts: stable fingerprints, dedup, nothing filed without an explicit submit."""

from __future__ import annotations

import json

import pytest

from integrations.trackers.base import DefectState
from results.api import ApiError, dispatch
from results.models import ResultRecord, RunRecord
from results.store import open_store
from tests.traceability._runs import RunSpec, T, red, write_artifacts
from traceability import api  # noqa: F401 - registers the routes
from traceability.cli import main as trace_main
from traceability.defects import (
    autofile_allowed,
    draft_from_failure,
    failures_from_reports,
    file_drafts,
    fingerprint,
    normalise_signature,
)

M = "tests/test_m.py::"


def test_fingerprint_ignores_volatile_parts():
    a = (
        "order 1234 total 12.40 != 12.41 at 2026-01-01T10:00:00Z id "
        + "3f2a9c1e-1111-2222-3333-"
        + "444455556666"
    )
    b = (
        "order 9876 total 99.10 != 99.11 at 2027-05-05T11:11:11Z id "
        + "0b1c2d3e-aaaa-bbbb-cccc-"
        + "ddddeeeeffff"
    )
    assert fingerprint(M + "t", a) == fingerprint(M + "t", b)
    assert fingerprint(M + "t", a) != fingerprint(M + "u", a)
    assert fingerprint(M + "t", "totals differ") != fingerprint(M + "t", "page did not load")
    assert "<n>" in normalise_signature("took 12 ms")


def test_draft_body_and_kinds(tmp_path):
    bundle = tmp_path / "b.json"
    bundle.write_text(
        json.dumps(
            {
                "test": "test_t",
                "error": "AssertionError: totals differ",
                "failedRequests": ["POST /api/orders -> 500"],
            }
        )
    )
    d = draft_from_failure(bundle, nodeid=M + "test_t", run_id="r1")
    assert d.title.startswith("test_t: AssertionError") and "POST /api/orders" in d.body
    assert f"defect-fingerprint: {d.fingerprint}" in d.body and d.kind == "failure"
    row = {
        "nodeid": M + "x",
        "ticket": "BUG-3",
        "verdict": "closed_regressed",
        "detail": "totals differ by a cent",
    }
    r = draft_from_failure(None, row, run_id="r1")
    assert r.kind == "closed_regressed" and "regression" in r.labels and "BUG-3" in r.title


class _Tracker:
    def __init__(self, existing=None):
        self.existing, self.created = existing or {}, []

    def find_by_fingerprint(self, fp):
        return self.existing.get(fp)

    def create(self, draft):
        self.created.append(draft)
        return DefectState(f"GH-{len(self.created)}", "open")


def test_nothing_is_filed_without_submit():
    tracker = _Tracker()
    drafts = [draft_from_failure(None, nodeid=M + "a", message="boom")]
    out = file_drafts(drafts, tracker, submit=False)
    assert [o.action for o in out] == ["drafted"] and tracker.created == []


def test_dedup_finds_the_existing_issue_and_flaky_is_skipped():
    a = draft_from_failure(None, nodeid=M + "a", message="boom 1")
    b = draft_from_failure(None, nodeid=M + "b", message="other")
    c = draft_from_failure(None, nodeid=M + "c", message="flaky thing")
    tracker = _Tracker({a.fingerprint: DefectState("GH-9", "open")})
    out = file_drafts([a, b, c], tracker, submit=True, exclude=[M + "c"])
    assert [(o.action, o.ref) for o in out] == [
        ("exists", "GH-9"),
        ("filed", "GH-1"),
        ("skipped", None),
    ]


def test_autofile_is_off_by_default_and_main_only():
    assert not autofile_allowed({})
    assert not autofile_allowed({"DEFECT_AUTOFILE": "true", "GITHUB_REF": "refs/heads/feature"})
    assert autofile_allowed({"DEFECT_AUTOFILE": "true", "GITHUB_REF": "refs/heads/main"})


def test_failures_from_reports_and_cli_draft(tmp_path, capsys):
    run = RunSpec(
        "r1",
        [T(M + "a", "failed"), T(M + "b"), T(M + "c", "error")],
        verdicts=[red("r1", M + "b", "BUG-3", "closed_regressed")],
    )
    reports = write_artifacts(tmp_path / "reports", [run])
    (reports / "job0" / "failures").mkdir()
    (reports / "job0" / "failures" / "a.json").write_text(
        json.dumps({"test": "a", "error": "boom"})
    )
    found = {nodeid: (msg, row) for _, nodeid, msg, row in failures_from_reports(reports)}
    assert found[M + "a"] == ("boom", None) and found[M + "b"][1]["verdict"] == "closed_regressed"
    out = tmp_path / "drafts"
    assert trace_main(["defects", "draft", "--from-run", str(reports), "--out", str(out)]) == 0
    assert len(json.loads((out / "drafts.json").read_text())) == 3
    assert "submit=no" in capsys.readouterr().out


def test_cli_sync_with_the_file_tracker(tmp_path, capsys):
    (tmp_path / "defects.yaml").write_text(
        "defects:\n  - {id: BUG-1, title: a, state: closed}\n"
        "  - {id: BUG-2, title: b, state: open}\n"
    )
    cfg = tmp_path / "trackers.yaml"
    cfg.write_text(
        f"trackers:\n  f: {{type: file, path: {tmp_path / 'defects.yaml'}, prefixes: [BUG]}}\n"
    )
    snap = tmp_path / "snap.json"
    db = tmp_path / "r.db"
    rc = trace_main(
        [
            "defects",
            "sync",
            "--no-collect",
            "--ids",
            "BUG-1",
            "--trackers",
            str(cfg),
            "--snapshot",
            str(snap),
            "--db",
            str(db),
        ]
    )
    assert rc == 0 and "closed: BUG-1" in capsys.readouterr().out
    with open_store(str(db)) as store:
        assert [
            d["defect_id"]
            for d in dispatch(store, "GET", "/defects", params={"state": "open"})["defects"]
        ] == ["BUG-2"]
        assert dispatch(store, "GET", "/defects/BUG-1")["defect"]["state"] == "closed"
        with pytest.raises(ApiError):
            dispatch(store, "GET", "/defects/BUG-404")


def test_rest_draft_and_case_link(tmp_path):
    from results.models import CaseRecord

    with open_store(str(tmp_path / "r.db")) as store:
        store.begin_run(RunRecord("r1"))
        store.upsert_cases([CaseRecord("c-1", "tests/test_m.py::a")])
        store.add_results(
            [
                ResultRecord("r1", M + "a", "failed", message="totals differ"),
                ResultRecord("r1", M + "b", "passed"),
            ]
        )
        draft = dispatch(store, "POST", "/defects/drafts", body={"run_id": "r1", "nodeid": M + "a"})
        assert draft["fingerprint"] == fingerprint(M + "a", "totals differ")
        with pytest.raises(ApiError):
            dispatch(store, "POST", "/defects/drafts", body={"run_id": "r1", "nodeid": M + "b"})
        dispatch(store, "PUT", "/cases/c-1/defects/GH-57")
        assert dispatch(store, "GET", "/defects/GH-57")["cases"] == ["c-1"]
        with pytest.raises(ApiError):
            dispatch(store, "PUT", "/cases/c-1/defects/REQ-1")
