"""Detection and the ratchet: a new gap fails, a closed gap passes with a reminder."""

from __future__ import annotations

import json

import pytest

from results.api import ApiError, dispatch
from results.store import open_store
from traceability import api  # noqa: F401 - registers the routes
from traceability.cli import main as trace_main
from traceability.endpoint_hits import EndpointHit
from traceability.gaps import (
    OpenApiSource,
    detect_gaps,
    ratchet,
    report_json,
    write_baseline,
)

pytest_plugins = ["pytester"]

OPENAPI = {
    "paths": {
        "/api/orders": {"get": {}, "post": {}},
        "/api/orders/{id}": {"get": {}},
    }
}


@pytest.fixture
def openapi(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(OPENAPI))
    return path


def _items(*tests):
    return [{"nodeid": n, "covers": c, "requirements": []} for n, c in tests]


def test_static_runtime_and_not_passing(openapi):
    links = _items(("t::a", ["GET /api/orders/{order_id}"]), ("t::b", ["POST /api/orders"]))
    hits = [EndpointHit("GET", "/api/orders", 200, "t::c")]
    report = detect_gaps(
        [OpenApiSource(openapi)], links, hits, {"t::a": "passed", "t::b": "failed"}
    )
    status = {i.item.key: i.status for i in report.items}
    assert status == {
        "GET /api/orders": "exercised",
        "POST /api/orders": "covered_not_passing",
        "GET /api/orders/{id}": "covered",
    }
    doc = report_json(report)
    assert doc["mode"] == "runtime" and doc["counts"]["gap"] == 0


def test_ratchet_new_gap_fails_closed_gap_fails_until_deleted(openapi, tmp_path):
    baseline = tmp_path / "baseline.json"
    links = _items(("t::a", ["GET /api/orders/{id}"]))
    first = detect_gaps([OpenApiSource(openapi)], links)
    write_baseline(first, baseline)
    assert ratchet(detect_gaps([OpenApiSource(openapi)], links), baseline).ok

    # positive control: one more uncovered operation flips the result
    grown = dict(OPENAPI["paths"], **{"/api/refunds": {"post": {}}})
    openapi.write_text(json.dumps({"paths": grown}))
    result = ratchet(detect_gaps([OpenApiSource(openapi)], links), baseline)
    assert not result.ok and result.new_gaps == ["openapi:POST /api/refunds"]

    # Strict (the default, changed on purpose): a closed gap still in the baseline fails
    # until the entry is deleted, so the baseline can only shrink.
    openapi.write_text(json.dumps(OPENAPI))
    more = _items(("t::a", ["GET /api/orders/{id}"]), ("t::b", ["POST /api/orders"]))
    report = detect_gaps([OpenApiSource(openapi)], more)
    result = ratchet(report, baseline)
    assert not result.ok and result.closed_gaps == ["openapi:POST /api/orders"]
    assert "stale baseline entry, delete it: openapi:POST /api/orders" in result.messages()
    assert report.ratchet == {"new": [], "closed": ["openapi:POST /api/orders"], "passed": False}

    # --allow-stale: the old behaviour, a reminder only
    relaxed = ratchet(detect_gaps([OpenApiSource(openapi)], more), baseline, strict=False)
    assert relaxed.ok and "remove them from the baseline" in relaxed.messages()[0]


def test_the_repository_baseline_is_readable():
    """The committed baseline loads; whether it still holds is the CI step's job, not a unit
    test's (a new demo route must fail the gaps step, not an unrelated test run)."""
    from pathlib import Path

    from traceability.gaps import load_baseline

    root = Path(__file__).resolve().parents[2]
    accepted = load_baseline(root / "traceability" / "gap-baseline.json")
    assert accepted and all(":" in ref for ref in accepted)


TREE = """
import pytest

@pytest.mark.covers("GET /api/orders")
def test_list():
    pass

@pytest.mark.requirement("REQ-1")
def test_req():
    pass
"""


def test_cli_end_to_end_with_covers_markers(pytester, subprocess_env, openapi, tmp_path, capsys):
    from tests.traceability.conftest import REGISTRY

    pytester.makefile(".yaml", **{"requirements/requirements": REGISTRY})
    pytester.makepyfile(test_tree=TREE)
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps({"accepted": ["openapi:POST /api/orders"]}))
    out = tmp_path / "out"
    db = tmp_path / "r.db"
    rc = trace_main(
        [
            "gaps",
            "--openapi",
            str(openapi),
            "--requirements",
            "--rootdir",
            str(pytester.path),
            "--baseline",
            str(baseline),
            "--out",
            str(out),
            "--db",
            str(db),
            "--branch",
            "main",
            "--run-id",
            "r1",
        ]
    )
    printed = capsys.readouterr().out
    assert rc == 1 and "NEW GAP: openapi:GET /api/orders/{id}" in printed
    doc = json.loads((out / "gaps.json").read_text())
    covered = {i["key"] for i in doc["items"] if i["status"] == "covered"}
    assert covered == {"GET /api/orders", "REQ-1"}
    assert "New gaps" in (out / "gaps.md").read_text()
    with open_store(str(db)) as store:
        assert (
            dispatch(store, "GET", "/coverage-gaps/latest", params={"branch": "main"})["run_id"]
            == "r1"
        )
        assert dispatch(store, "GET", "/runs/r1/coverage-gaps")["counts"]["covered"] == 2
        with pytest.raises(ApiError):
            dispatch(store, "GET", "/runs/r9/coverage-gaps")
    rc = trace_main(
        [
            "gaps",
            "--openapi",
            str(openapi),
            "--rootdir",
            str(pytester.path),
            "--baseline",
            str(baseline),
            "--report-only",
            "--out",
            str(out),
        ]
    )
    assert rc == 0


def test_cli_needs_a_source(capsys):
    assert trace_main(["gaps"]) == 2


def test_gaps_per_group_with_an_ungrouped_row(openapi):
    from traceability.gaps import group_gaps

    report = detect_gaps([OpenApiSource(openapi)], _items(("t::a", ["GET /api/orders/{id}"])))
    rows = group_gaps(report, {"orders-read": ["openapi:GET "], "none": ["tag:REQ-1"]})
    assert rows == [
        {"group": "orders-read", "items": 2, "uncovered": 1},
        {"group": "none", "items": 0, "uncovered": 0},
        {"group": "(ungrouped)", "items": 1, "uncovered": 1},
    ]
    report.groups = rows
    assert "| (ungrouped) | 1 | 1 |" in report.to_markdown()
    assert report_json(report)["ratchet"] is None  # the views never feed the ratchet


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_frozen_ref_lists_items_present_at_only_one_side(tmp_path):
    from traceability.gaps import frozen_view

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    spec = repo / "openapi.json"
    spec.write_text(json.dumps(OPENAPI))
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "a")
    _git(repo, "tag", "v1")
    grown = dict(OPENAPI["paths"], **{"/api/refunds": {"post": {}}})
    spec.write_text(json.dumps({"paths": grown}))
    head = detect_gaps([OpenApiSource(spec)], [])
    view = frozen_view(head, "v1", root=repo, openapi=[str(spec)])
    assert view == {
        "ref": "v1",
        "measured": True,
        "gaps": 3,
        "only_head": ["openapi:POST /api/refunds"],
        "only_ref": [],
    }
    missing = frozen_view(head, "v1", root=repo, openapi=[str(repo / "absent.json")])
    assert missing["measured"] is False and "not at v1" in missing["reason"]
