"""Rendering: the headline never hides that nothing is proved; JSON matches its schema."""

from __future__ import annotations

import json

from tests.traceability._runs import RunSpec, T, coverage, registry, write_artifacts
from traceability.cli import main as trace_main
from traceability.evidence import ArtifactResultSource
from traceability.matrix import build_matrix, matrix_json, render_markdown

M = "tests/test_m.py::"


def test_a_matrix_with_nothing_proved_says_so_in_its_headline(tmp_path):
    write_artifacts(tmp_path, [RunSpec("r1", [T(M + "a", reqs=("REQ-1",))])])
    m = build_matrix(registry("REQ-1", "REQ-2"), None, ArtifactResultSource(tmp_path))
    assert m.headline.startswith("NOTHING IS PROVED: 0 of 2")
    assert "1 failing or uncovered" in m.headline
    md = render_markdown(m)
    assert "**NOTHING IS PROVED" in md and "| REQ-2 | title REQ-2 | UNCOVERED | none linked |" in md
    assert "Staleness not measured" in md


def test_proved_headline_and_schema(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1",))], verdicts=[coverage("r1", M + "a", "REQ-1")])
    write_artifacts(tmp_path, [run])
    m = build_matrix(registry("REQ-1"), None, ArtifactResultSource(tmp_path))
    assert m.headline == "1 of 1 requirements proved"
    doc = matrix_json(m)  # validates
    assert doc["counts"]["PROVED"] == 1 and doc["run_ids"] == ["r1"]


def test_no_requirements_in_scope(tmp_path):
    m = build_matrix(registry(), None, ArtifactResultSource(tmp_path))
    assert m.headline == "NO REQUIREMENTS IN SCOPE"


def test_cli_writes_json_and_markdown(tmp_path, capsys):
    reports = tmp_path / "reports"
    write_artifacts(reports, [RunSpec("r1", [T(M + "a", reqs=("REQ-1",))])])
    reg = tmp_path / "reg.yaml"
    reg.write_text("requirements:\n  - {id: REQ-1, title: one, priority: must}\n")
    out = tmp_path / "out"
    rc = trace_main(
        ["matrix", "--reports", str(reports), "--registry", str(reg), "--out", str(out)]
    )
    assert rc == 0
    assert json.loads((out / "matrix.json").read_text())["rows"][0]["status"] == "PASSING"
    assert (out / "matrix.md").read_text().startswith("## Requirements traceability")
    assert "NOTHING IS PROVED" in capsys.readouterr().out


def test_cli_refuses_an_unscoped_store_matrix(tmp_path, capsys):
    rc = trace_main(["matrix", "--db", str(tmp_path / "r.db"), "--out", str(tmp_path)])
    assert rc == 2 and "unscoped" in capsys.readouterr().err


def test_cli_writes_csv_and_a_headline_line(tmp_path, capsys):
    reports = tmp_path / "reports"
    write_artifacts(
        reports, [RunSpec("r1", [T(M + "a", reqs=("REQ-1",)), T(M + "b", reqs=("REQ-1",))])]
    )
    reg = tmp_path / "reg.yaml"
    reg.write_text(
        "requirements:\n  - {id: REQ-1, title: one, priority: must}\n"
        "  - {id: REQ-2, title: two, priority: must}\n"
    )
    out = tmp_path / "out"
    assert (
        trace_main(["matrix", "--reports", str(reports), "--registry", str(reg), "--out", str(out)])
        == 0
    )
    lines = (out / "matrix.csv").read_text().splitlines()
    assert lines[0] == (
        "requirement,status,nodeid,outcome,run_id,coverage_verdict,detector_verdict,defects"
    )
    assert lines[1:] == [
        "REQ-2,UNCOVERED,,,,,,",
        f"REQ-1,PASSING,{M}a,passed,r1,,,",
        f"REQ-1,PASSING,{M}b,passed,r1,,,",
    ]
    headline = (out / "headline.txt").read_text()
    assert headline == "MATRIX requirements=2 results=2 UNCOVERED=1 PASSING=1\n"


def test_a_headline_with_no_results_says_no_evidence(tmp_path):
    m = build_matrix(registry("REQ-1"), None, ArtifactResultSource(tmp_path))
    assert m.headline_line == "MATRIX requirements=1 results=0 UNCOVERED=1 NO-EVIDENCE"
    assert "No results were read" in render_markdown(m)


def test_a_link_to_an_epic_is_listed_as_too_coarse(tmp_path):
    from traceability.requirements import Registry, Requirement

    write_artifacts(tmp_path, [RunSpec("r1", [T(M + "a", reqs=("REQ-1", "REQ-2"))])])
    reg = Registry.from_requirements(
        [Requirement("REQ-1", "billing", kind="epic"), Requirement("REQ-2", "rounding")]
    )
    m = build_matrix(reg, None, ArtifactResultSource(tmp_path))
    assert m.findings == [
        {"nodeid": M + "a", "id": "REQ-1", "kind": "epic", "finding": "link too coarse"}
    ]
    assert "Links too coarse" in render_markdown(m)
    plain = build_matrix(registry("REQ-1", "REQ-2"), None, ArtifactResultSource(tmp_path))
    assert plain.findings == []  # a registry without kind produces no finding
