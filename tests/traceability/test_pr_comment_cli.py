"""The CLI never posts without --post, never posts from a fork, always writes the summary."""

from __future__ import annotations

import json

import pytest

from results.api import dispatch
from results.models import ResultRecord, RunRecord
from results.store import open_store
from tests.traceability._runs import RunSpec, T, write_artifacts
from traceability import api  # noqa: F401 - registers the routes
from traceability.cli import main as trace_main


@pytest.fixture
def reports(tmp_path):
    return write_artifacts(tmp_path / "reports", [RunSpec("r1", [T("t::a")])])


def test_writes_the_file_and_the_step_summary(tmp_path, reports, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "pr.md"
    assert trace_main(["pr-comment", "--reports", str(reports), "--out", str(out)]) == 0
    assert out.read_text() == summary.read_text()
    assert "run trust default PASS" in out.read_text()


def test_a_fork_gets_the_step_summary_only(tmp_path, reports, monkeypatch, capsys):
    event = tmp_path / "e.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 3,
                    "head": {"repo": {"full_name": "x/fork"}},
                    "base": {"repo": {"full_name": "o/r"}},
                }
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")

    def boom(*a, **k):
        raise AssertionError("must not post from a fork")

    monkeypatch.setattr("integrations.github.upsert_pr_comment", boom)
    rc = trace_main(
        ["pr-comment", "--reports", str(reports), "--out", str(tmp_path / "x.md"), "--post"]
    )
    assert rc == 0 and "step summary only" in capsys.readouterr().out


def test_missing_token_is_not_a_failure(tmp_path, reports, capsys):
    rc = trace_main(
        [
            "pr-comment",
            "--reports",
            str(reports),
            "--out",
            str(tmp_path / "x.md"),
            "--post",
            "--pr",
            "4",
        ]
    )
    assert rc == 0 and "not posted" in capsys.readouterr().out


def test_rest_summary(tmp_path):
    with open_store(str(tmp_path / "r.db")) as store:
        store.begin_run(RunRecord("r1", integrity_verdict="FAIL", lane="browser"))
        store.add_results([ResultRecord("r1", "t::a", "failed")])
        doc = dispatch(store, "GET", "/runs/r1/summary")
    assert "RUN TRUST FAILED (browser)" in doc["body"]
