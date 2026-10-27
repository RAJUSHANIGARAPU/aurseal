"""detect-coverage-gaps: a thin wrapper over the CI ratchets, with the CI's own sources."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
import yaml

from tests.tools.slash._commands import ROOT, claude_md_lists, read_command
from tools.slash import coverage_gaps as cg


def ci_step(name: str) -> list[str]:
    """The arguments after ``python -m traceability`` in the offline job's step ``name``."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps") or ():
            if step.get("name") == name:
                words = shlex.split(step["run"])
                assert words[:3] == ["python", "-m", "traceability"], step["run"]
                return words[3:]
    raise AssertionError(f"no CI step named {name!r}")


def test_the_command_runs_the_ci_ratchets_with_their_sources():
    gaps, workflow = cg.commands("all", Path("out"))
    assert gaps == [*ci_step("Coverage gaps (ratchet)"), "--report-only", "--out", "out"]
    assert workflow == [
        *ci_step("Workflow action gaps (ratchet)"),
        "--report-only",
        "--out",
        "out",
    ]


def test_an_extra_source_is_one_more_input(tmp_path):
    collection = tmp_path / "c.json"
    collection.write_text(json.dumps({"info": {}, "item": []}))
    document = tmp_path / "o.json"
    document.write_text(json.dumps({"paths": {}}))
    assert cg.extra_source("all") == []
    assert cg.extra_source("https://api.example.test/openapi.json") == [
        "--openapi",
        "https://api.example.test/openapi.json",
    ]
    assert cg.extra_source(str(collection)) == ["--postman", str(collection)]
    assert cg.extra_source(str(document)) == ["--openapi", str(document)]
    with pytest.raises(ValueError, match="not a file"):
        cg.extra_source(str(tmp_path / "missing.json"))
    gaps, _ = cg.commands(str(collection), tmp_path)
    assert gaps[-5:] == ["--postman", str(collection), "--report-only", "--out", str(tmp_path)]


def test_a_bad_target_exits_two(tmp_path, capsys):
    assert cg.main([str(tmp_path / "nope.json"), "--out", str(tmp_path)]) == 2
    assert "not a file or an http(s) URL" in capsys.readouterr().err


def test_the_command_asks_before_generating_and_may_be_model_invoked():
    meta, body = read_command("detect-coverage-gaps")
    assert "disable-model-invocation" not in meta
    assert claude_md_lists("detect-coverage-gaps")
    assert "explicit yes" in body and "Without a yes, nothing is generated" in body
    assert "python -m tools.slash.coverage_gaps" in body and "--report-only" in body
    assert "GAPS= ACTIONS= NEW= STALE=" in body and "NOT-MEASURED" in body
