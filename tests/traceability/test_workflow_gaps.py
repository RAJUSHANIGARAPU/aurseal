"""Workflow action gaps: (state, action) pairs, string-literal naming, the two-way ratchet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traceability._util import validate
from traceability.cli import main as trace_main
from traceability.workflow_gaps import (
    NOT_MEASURED,
    BaselineError,
    find_test_modules,
    load_baseline,
    measure,
    report_json,
    string_literals,
)

MACHINE = """\
key: door
initial_states: [shut]
states:
  shut:
    transitions:
      - {action: unlock, to: open}
      - {action: force, to: open}
      - {to: open, automatic: true}
  open:
    transitions:
      - {action: unlock, to: open}
      - {action: slam, to: shut}
  gone:
    title: terminal
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "door.machine.yaml").write_text(MACHINE)
    (config / "notes.yaml").write_text("just: a mapping\n")  # not a workflow: skipped
    tests = tmp_path / "tests"
    (tests / "sub").mkdir(parents=True)
    (tests / "test_door.py").write_text(
        'ACTION = "unlock"\n\ndef test_x():\n    assert "slam the door"\n'
    )
    (tests / "sub" / "door_test.py").write_text("STEPS = ['open', 'shut']\n")
    (tests / "test_broken.py").write_text('NAME = "force"\ndef broken(:\n')
    (tests / "helpers.py").write_text('NAME = "force"\n')  # not a test module
    return tmp_path


def run(tree: Path, *extra: str) -> int:
    return trace_main(
        [
            "workflow-gaps",
            "--config-dir",
            str(tree / "config"),
            "--tests",
            str(tree / "tests"),
            "--out",
            str(tree / "out"),
            *extra,
        ]
    )


def test_pairs_are_named_only_by_a_whole_literal_in_a_test_module(tree):
    modules = find_test_modules([tree / "tests", tree / "missing"])
    assert {p.name for p in modules} == {"test_broken.py", "test_door.py", "door_test.py"}
    assert "unlock" in string_literals(modules) and "force" not in string_literals(modules)
    report = measure(tree / "config", [tree / "tests"])
    assert [p.ref for p in report.pairs] == [
        "door:shut:unlock",
        "door:shut:force",
        "door:open:unlock",
        "door:open:slam",
    ]
    assert report.gaps == ["door:shut:force", "door:open:slam"]
    assert report.header() == "GAPS=2 ACTIONS=4 NEW=0 STALE=0"


def test_a_new_gap_and_a_stale_entry_both_fail(tree, capsys):
    baseline = tree / "baseline.json"
    baseline.write_text(json.dumps({"accepted": ["door:shut:force", "door:gone:wave"]}))
    assert run(tree, "--baseline", str(baseline)) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "GAPS=2 ACTIONS=4 NEW=1 STALE=1"
    assert "NEW GAP: door:open:slam" in out and "STALE: door:gone:wave" in out
    md = (tree / "out" / "workflow-gaps.md").read_text()
    assert md.startswith("GAPS=2 ACTIONS=4 NEW=1 STALE=1\n") and "Stale baseline" in md
    doc = json.loads((tree / "out" / "workflow-gaps.json").read_text())
    assert doc["new"] == ["door:open:slam"] and doc["stale"] == ["door:gone:wave"]
    assert run(tree, "--baseline", str(baseline), "--report-only") == 0

    # the baseline rewritten from the current state holds; naming an accepted gap makes
    # its entry stale, so the baseline has to shrink with it
    assert run(tree, "--baseline", str(baseline), "--write-baseline") == 0
    assert load_baseline(baseline) == {"door:shut:force", "door:open:slam"}
    assert run(tree, "--baseline", str(baseline)) == 0
    (tree / "tests" / "test_slam.py").write_text('WHAT = "slam"\n')
    assert run(tree, "--baseline", str(baseline)) == 1
    assert "STALE: door:open:slam is no longer a gap" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda t: None, "no config dir given"),
        (lambda t: t / "absent", "config dir not found"),
        (lambda t: (t / "empty").mkdir() or t / "empty", "no workflow definition"),
        (
            lambda t: (
                ((t / "bad").mkdir(), (t / "bad" / "x.yaml").write_text("a: [")) and t / "bad"
            ),
            "does not parse",
        ),
    ],
)
def test_an_unbound_config_dir_is_not_measured_never_zero(
    tmp_path, capsys, monkeypatch, setup, reason
):
    monkeypatch.delenv("EXT_CONFIG_PATH", raising=False)  # CI binds it before the suite runs
    config = setup(tmp_path)
    args = ["workflow-gaps", "--out", str(tmp_path / "out"), "--tests", str(tmp_path)]
    if config is not None:
        args += ["--config-dir", str(config)]
    assert trace_main(args) == 3
    out = capsys.readouterr().out
    assert out.splitlines()[0] == " ".join(
        f"{k}={NOT_MEASURED}" for k in ("GAPS", "ACTIONS", "NEW", "STALE")
    )
    assert reason in out
    doc = json.loads((tmp_path / "out" / "workflow-gaps.json").read_text())
    assert doc["measured"] is False and doc["gaps"] is None and doc["actions"] is None
    assert trace_main([*args, "--report-only"]) == 0
    capsys.readouterr()


def test_the_bound_checkout_variable_is_the_fallback(tree, monkeypatch):
    monkeypatch.setenv("EXT_CONFIG_PATH", str(tree / "config"))
    assert measure(None, [tree / "tests"]).header() == "GAPS=2 ACTIONS=4 NEW=0 STALE=0"


def test_a_bad_baseline_is_an_input_error(tree, capsys):
    baseline = tree / "baseline.json"
    baseline.write_text("[1]")
    with pytest.raises(BaselineError):
        load_baseline(baseline)
    assert run(tree, "--baseline", str(baseline)) == 2
    assert "a baseline is" in capsys.readouterr().err


def test_the_json_matches_its_schema_both_ways(tree):
    validate(
        report_json(measure(tree / "config", [tree / "tests"])), "workflow-gap-report.schema.json"
    )
    unmeasured = measure(tree / "absent").to_json()
    validate(unmeasured, "workflow-gap-report.schema.json")
    with pytest.raises(Exception, match="workflow-gap-report"):
        validate({**unmeasured, "gaps": []}, "workflow-gap-report.schema.json")


def test_the_repository_baseline_is_readable():
    root = Path(__file__).resolve().parents[2]
    assert load_baseline(root / "traceability" / "workflow-gap-baseline.json") is not None
