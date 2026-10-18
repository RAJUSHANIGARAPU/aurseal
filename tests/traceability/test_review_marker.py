"""Draft tests: out of the gate, visible in their own lane, never evidence."""

from __future__ import annotations

import json
import subprocess
import sys

from gate.lanes import excluded_markers, gate_marker_expression
from results.api import dispatch
from results.catalogue import build_catalogue, store_catalogue
from results.store import open_store
from tests.traceability.conftest import PLUGIN, REGISTRY, ROOT
from traceability import api  # noqa: F401 - registers the routes
from traceability.cli import main as trace_main
from traceability.evidence import ArtifactResultSource
from traceability.links import load_case_links
from traceability.matrix import Status, build_matrix
from traceability.requirements import Registry
from traceability.review import mark_draft

pytest_plugins = ["pytester"]

APPROVED = """
import pytest

@pytest.mark.requirement("REQ-1")
def test_reviewed():
    pass
"""
DRAFT = mark_draft(
    'import pytest\n\n\n@pytest.mark.requirement("REQ-2")\ndef test_generated():\n    pass\n',
    "spec",
    "specs/orders.md",
    "2026-01-01T00:00:00Z",
)


def _tree(pytester):
    pytester.makefile(".yaml", **{"requirements/requirements": REGISTRY})
    pytester.makepyfile(test_approved=APPROVED, test_draft=DRAFT)


def test_draft_is_excluded_from_the_gate_expression():
    assert "draft" in excluded_markers({})
    assert "not draft" in gate_marker_expression("smoke", {})


def test_the_gate_expression_runs_only_reviewed_tests(pytester, subprocess_env):
    _tree(pytester)
    expr = gate_marker_expression(env={})
    result = pytester.runpytest_subprocess(*PLUGIN, "-m", expr)
    result.assert_outcomes(passed=1, deselected=1)
    # the drafts lane still runs them, so they stay visible
    pytester.runpytest_subprocess(*PLUGIN, "-m", "draft").assert_outcomes(passed=1, deselected=1)


def test_a_draft_result_is_never_evidence(pytester, subprocess_env, monkeypatch, tmp_path):
    out = pytester.path / "reports"
    monkeypatch.setenv("TRACEABILITY_DIR", str(out / "traceability"))
    monkeypatch.setenv("RUN_TRUST_DIR", str(out))
    monkeypatch.setenv("RUN_TRUST", "report")
    _tree(pytester)
    pytester.runpytest_subprocess(*PLUGIN, "-p", "gate.pytest_plugin").assert_outcomes(passed=2)
    links = load_case_links(out / "traceability" / "case-links.json")
    entry = links.tests["test_draft.py::test_generated"]
    assert entry["review_state"] == "draft" and entry["generator"] == "spec"
    registry = Registry.load(pytester.path / "requirements" / "requirements.yaml")
    matrix = build_matrix(registry, None, ArtifactResultSource(out))
    assert matrix.row("REQ-2").status is Status.DRAFT_ONLY
    assert matrix.row("REQ-1").status is Status.PASSING


def test_review_queue_from_collection_and_store(pytester, subprocess_env, tmp_path, capsys):
    _tree(pytester)
    assert trace_main(["review", "queue", "--rootdir", str(pytester.path)]) == 0
    printed = capsys.readouterr().out
    assert "test_draft.py::test_generated  generator=spec" in printed
    assert "1 test(s) awaiting review" in printed
    with open_store(str(tmp_path / "r.db")) as store:
        store_catalogue(store, build_catalogue(pytester.path))
        queue = dispatch(store, "GET", "/review/drafts")
    assert [c["case_key"] for c in queue["cases"]] == ["test_draft.py::test_generated"]


def test_the_marker_is_registered_for_strict_markers(pytester, subprocess_env):
    _tree(pytester)
    result = pytester.runpytest_subprocess(*PLUGIN, "--strict-markers")
    result.assert_outcomes(passed=2)


def test_cli_entry_point_runs():
    proc = subprocess.run(
        [sys.executable, "-m", "traceability", "review", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0 and "lint" in proc.stdout
    assert json.dumps({"ok": True})
