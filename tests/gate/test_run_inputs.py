"""Manual-run inputs -> validated gate command (gate.run_inputs)."""

from __future__ import annotations

import io
import shlex
from pathlib import Path

import pytest
import yaml

from gate import run_inputs
from gate.run_inputs import INPUTS, RunInputsError, default_command, main, plan

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
ENVS = ["local", "qa", "staging"]


def _plan(**values):
    return plan(values, environments=ENVS, root=ROOT)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job: str, name: str) -> dict:
    return next(s for s in _workflow()["jobs"][job]["steps"] if s.get("name") == name)


def test_default_inputs_produce_the_literal_gate_step():
    assert _plan().argv == default_command()
    assert shlex.split(_step("browser", "Run Smoke Tests")["run"]) == default_command()
    assert default_command() == [
        "pytest",
        "-m",
        "(smoke or api or contract) and not unit and not on_demand and not slow and not draft"
        " and not render_check",
        "-n",
        "auto",
        "--browser-channel=chrome",
        "--junitxml=reports/junit-browser.xml",
    ]
    assert _plan().env == {"TEST_ENV": "qa"} and _plan().is_default()


def test_presets():
    smoke = _plan(preset="smoke").argv
    assert smoke[2].startswith("(smoke) and not unit") and "not render_check" in smoke[2]
    assert _plan(preset="all").argv == default_command()
    with pytest.raises(RunInputsError, match="preset"):
        _plan(preset="everything")


def test_opt_in_lane_is_removed_from_the_exclusions_and_toggled():
    result = _plan(lanes="slow")
    assert "not slow" not in result.argv[2]
    assert result.env["RUN_SLOW_TESTS"] == "true"
    assert _plan(lanes="regression, checks").argv == default_command()
    with pytest.raises(RunInputsError, match=r"unknown lane\(s\) \['typo'\]"):
        _plan(lanes="slow,typo")


def test_seed_inputs():
    with pytest.raises(RunInputsError, match="needs input entity_allowlist"):
        _plan(seed_mode="targets")
    with pytest.raises(RunInputsError, match="would be ignored"):
        _plan(entity_allowlist="a,b")
    with pytest.raises(RunInputsError, match="would be ignored"):
        _plan(seed_mode="reuse", entity_allowlist="a")
    assert _plan(seed_mode="targets", entity_allowlist=" a , b ").env["ENTITY_ALLOWLIST"] == "a,b"
    with pytest.raises(RunInputsError, match="comma-separated"):
        _plan(seed_mode="targets", entity_allowlist="a,b;c")
    fresh = _plan(seed_mode="fresh")
    assert fresh.argv == default_command() and fresh.env == {"TEST_ENV": "qa"}


def test_token_rules_and_masking():
    with pytest.raises(RunInputsError, match="token would be ignored"):
        _plan(token="abc")
    with pytest.raises(RunInputsError, match="needs input token"):
        _plan(auth_mode="token")
    secret = "tok-123-not-real"
    result = _plan(auth_mode="token", token=secret)
    assert result.env["SUPPLIED_TOKEN"] == secret and result.env["AUTH_MODE"] == "token"
    assert all(secret not in arg for arg in result.argv)
    out = io.StringIO()
    env = {"INPUT_AUTH_MODE": "token", "INPUT_TOKEN": secret}
    assert main(["run", "--dry-run"], environ=env, out=out) == 0
    assert secret not in out.getvalue() and "***" in out.getvalue()


def test_selection_drops_exclusions_and_requires_verdicts(capsys):
    result = _plan(selection="tests/gate/test_lanes.py", preset="smoke")
    assert result.argv[:5] == ["pytest", "tests/gate/test_lanes.py", "-m", "", "-n"]
    assert result.argv[5] == "0" and "--reruns" not in result.argv
    assert result.env["COVERAGE_VERDICT_REQUIRED"] == "true"
    assert result.env["INCLUDE_MANUAL_CHECKS"] == "true"
    assert any("preset 'smoke' ignored" in n for n in result.notices)
    with pytest.raises(RunInputsError, match="selection"):
        _plan(selection="tests/no_such_file.py")


def test_undeclared_environment_lists_the_valid_ones():
    with pytest.raises(RunInputsError, match=r"valid: \['local', 'qa', 'staging'\]"):
        _plan(env="prod")


def test_other_inputs_map_to_the_variables_the_framework_reads():
    result = _plan(
        headless="false",
        step_screenshots="true",
        keep_all_evidence="true",
        strict_oracles="true",
        require_config_binding="true",
    )
    assert result.env == {
        "TEST_ENV": "qa",
        "HEADLESS": "false",
        "ALLURE_STEP_SCREENSHOTS": "true",
        "TRACE_ALWAYS": "true",
        "VIDEO_ALWAYS": "true",
        "STRICT_ORACLES": "true",
        "EXT_CONFIG_REQUIRED": "true",
    }
    with pytest.raises(RunInputsError, match="not a boolean"):
        _plan(headless="maybe")


def test_dry_run_does_not_spawn(tmp_path):
    called = []
    code = main(["run", "--dry-run"], environ={}, spawn=lambda *a: called.append(a) or 0)
    assert code == 0 and called == []
    assert main(["run"], environ={}, spawn=lambda argv, env: 7) == 7


def test_invalid_input_exits_2_before_anything_runs():
    called = []
    code = main(["run"], environ={"INPUT_ENV": "prod"}, spawn=lambda *a: called.append(a) or 0)
    assert code == 2 and called == []


def test_resolve_writes_env_and_custom_output(tmp_path):
    env_file, out_file = tmp_path / "env", tmp_path / "out"
    args = ["resolve", "--github-env", str(env_file), "--github-output", str(out_file)]
    assert main(args, environ={}, out=io.StringIO()) == 0
    assert "custom=false" in out_file.read_text() and "TEST_ENV<<" in env_file.read_text()
    main([*args, "--preset", "smoke"], environ={}, out=io.StringIO())
    assert "custom=true" in out_file.read_text()


def test_every_dispatch_input_is_read_and_every_read_input_is_declared():
    declared = set(_workflow()[True]["workflow_dispatch"]["inputs"])
    read = {item.name for item in INPUTS}
    assert declared == read
    resolve_env = _step("browser", "Resolve the run inputs")["env"]
    assert set(resolve_env) == {run_inputs.env_var(name) for name in read}


def test_dispatch_environment_options_are_declared_environments():
    options = _workflow()[True]["workflow_dispatch"]["inputs"]["env"]["options"]
    assert set(options) <= set(run_inputs.declared_environments())
