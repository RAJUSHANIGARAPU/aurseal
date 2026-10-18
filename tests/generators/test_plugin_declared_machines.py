"""The state-machine plugin reports declared workflow graphs next to what it discovers."""

from __future__ import annotations

from pathlib import Path

from tests.plugins._tier4 import load

MACHINE = Path(__file__).resolve().parents[2] / "demo_sut" / "config" / "subscription.machine.yaml"


def test_declared_machines_are_reported_with_their_unreachable_states(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    bad = tmp_path / "bad.yaml"
    bad.write_text("timeout: 3\n")
    plugin = load("state_machine_exhaustive").StateMachineExhaustivePlugin()
    result = plugin.dry_run({"source_dir": str(tmp_path / "src"), "workflow_files": [MACHINE, bad]})
    good, broken = result.findings[0]["declared_machines"]
    assert good["key"] == "subscription" and good["unreachable"] == ["suspended"]
    assert good["paths"] > 0 and good["problems"] == []
    assert broken["file"] == str(bad) and "error" in broken


def test_without_workflow_files_the_report_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugin = load("state_machine_exhaustive").StateMachineExhaustivePlugin()
    result = plugin.dry_run({"source_dir": str(tmp_path)})
    assert result.findings[0]["declared_machines"] == []
