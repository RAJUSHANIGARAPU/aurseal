"""links --check-prose: prose ids must be marker links; the baseline only shrinks."""

from __future__ import annotations

from pathlib import Path

from traceability.cli import main as trace_main
from traceability.prose import (
    ProseViolation,
    ids_in_name,
    ids_in_text,
    load_baseline,
    ratchet,
    violations,
)
from traceability.requirements import Namespace

FIXTURE = Path(__file__).parent / "prose_fixture"
PLANTED = "tests/traceability/prose_fixture/planted_prose.py::test_refund_is_rounded REQ-7"
ARGS = ["--", "-o", "python_files=planted_*.py", "planted_prose.py"]
NS = Namespace.from_env({})


def test_ids_are_found_in_names_and_text():
    assert ids_in_name("test_req_12_totals", NS) == ["REQ-12"]
    assert ids_in_name("test_case_1", NS) == []  # CASE is not a requirement prefix
    assert ids_in_text("Covers REQ-7 and BUG-3, see REQ-7", NS) == ["REQ-7"]


def test_a_marker_link_silences_a_prose_mention():
    items = [
        {"nodeid": "t.py::a[x]", "requirements": ["REQ-1"], "prose_ids": ["REQ-1", "REQ-2"]},
        {
            "nodeid": "t.py::b",
            "requirements": [],
            "invalid": [{"id": "REQ-9"}],
            "prose_ids": ["REQ-9"],
        },
    ]
    assert violations(items) == [ProseViolation("t.py::a", "REQ-2")]


def test_the_baseline_tolerates_old_mentions_and_only_shrinks(tmp_path):
    found = [ProseViolation("t.py::a", "REQ-2")]
    base = tmp_path / "b.txt"
    base.write_text("# comment\nt.py::a REQ-2\nt.py::gone REQ-5\n")
    result = ratchet(found, load_baseline(base))
    assert (result.new, result.stale, result.ok) == ([], ["t.py::gone REQ-5"], False)
    assert ratchet([*found, ProseViolation("t.py::c", "REQ-4")], {"t.py::a REQ-2"}).new == [
        "t.py::c REQ-4"
    ]


def test_the_checker_reports_the_planted_prose_id(capsys, tmp_path):
    # Positive control: a checker that sees nothing cannot pass this.
    empty = tmp_path / "baseline.txt"
    rc = trace_main(
        ["links", "--check-prose", "--rootdir", str(FIXTURE), "--baseline", str(empty), *ARGS]
    )
    out = capsys.readouterr().out
    assert rc == 1, out
    assert f"PROSE-ONLY ID: {PLANTED}" in out
    assert "1 prose-only id(s)" in out  # the linked mentions are not reported
    empty.write_text(PLANTED + "\n")
    assert (
        trace_main(
            ["links", "--check-prose", "--rootdir", str(FIXTURE), "--baseline", str(empty), *ARGS]
        )
        == 0
    )


def test_the_committed_baseline_holds():
    root = Path(__file__).resolve().parents[2]
    assert load_baseline(root / "traceability" / "prose-baseline.txt") == set()


def test_links_check_lists_a_link_to_an_epic_but_never_fails(tmp_path, capsys, monkeypatch):
    reg = tmp_path / "requirements" / "requirements.yaml"
    reg.parent.mkdir()
    reg.write_text(
        "requirements:\n  - {id: REQ-1, title: billing, kind: epic}\n"
        "  - {id: REQ-2, title: totals}\n  - {id: REQ-3, title: monthly}\n"
    )
    (tmp_path / "test_x.py").write_text(
        "import pytest\n\n@pytest.mark.requirement('REQ-1')\ndef test_a():\n    pass\n"
    )
    monkeypatch.setenv("REQUIREMENTS_FILE", str(reg))
    rc = trace_main(["links", "--check", "--strict", "--rootdir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "test_x.py::test_a: REQ-1 (link too coarse: kind epic, not a requirement)" in out
