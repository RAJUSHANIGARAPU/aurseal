"""Binding an external configuration checkout in CI (gate.external_config)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from gate import external_config
from gate.external_config import Binding, bound_checkout, check_sentinel, main, render_summary
from gate.integrity import check_marker

ROOT = Path(__file__).resolve().parents[2]
SENTINEL = "subscription.machine.yaml"


def test_sentinel_check(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / SENTINEL).write_text("key: x\n")
    assert check_sentinel(good).bound
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert check_sentinel(empty_dir) == Binding(False, "no sentinel", empty_dir)
    assert check_sentinel(tmp_path / "gone").reason == "missing"
    zero = tmp_path / "zero"
    zero.mkdir()
    (zero / SENTINEL).write_text("")
    assert not check_sentinel(zero).bound and "empty" in check_sentinel(zero).reason


def test_summary_renderer(tmp_path):
    bound = render_summary(Binding(True, "ok", tmp_path), "o/r", "main", "0123456789abcdef")
    assert bound == "External config bound: o/r@main commit 01234567"
    block = render_summary(
        Binding(False, "missing", tmp_path), "o/r", "main", checks=["states match"]
    )
    assert "NOT bound" in block and "- states match" in block and "wrong ref" in block


def _verify(tmp_path, checkout: Path, required: bool, capsys):
    environ = {"EXT_CONFIG_REQUIRED": "true" if required else "false", "TEST_RUN_ID": "r1"}
    code = main(
        [
            "verify",
            "--path",
            str(checkout),
            "--sub-path",
            "",
            "--repo",
            "o/r",
            "--ref",
            "main",
            "--marker-dir",
            str(tmp_path / "reports"),
            "--github-env",
            str(tmp_path / "github_env"),
            "--summary",
            str(tmp_path / "summary.md"),
        ],
        environ=environ,
    )
    return code, capsys.readouterr()


def test_cli_required_fails_and_optional_warns(tmp_path, capsys):
    code, captured = _verify(tmp_path, tmp_path / "absent", required=True, capsys=capsys)
    assert code == 1 and "EXT_CONFIG_REQUIRED" in captured.err
    code, captured = _verify(tmp_path, tmp_path / "absent", required=False, capsys=capsys)
    assert code == 0 and captured.out.startswith("::warning title=External config::")
    assert "NOT bound" in (tmp_path / "summary.md").read_text()
    # The lane verdict is FAIL/UNSTABLE: the run can no longer look fully green.
    marker = (tmp_path / "reports" / "run-trust-ext-config.txt").read_text()
    assert marker.startswith("VERDICT=FAIL ENFORCEMENT=UNSTABLE")
    assert check_marker(tmp_path / "reports", "ext-config") == 0


def test_cli_bound_exports_path_pin_and_commit(tmp_path, capsys):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / SENTINEL).write_text("key: x\n")
    git = ["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    code, captured = _verify(tmp_path, checkout, required=True, capsys=capsys)
    assert code == 0 and "External config bound: o/r@main commit" in captured.out
    exported = (tmp_path / "github_env").read_text()
    assert "EXT_CONFIG_PINNED=true" in exported and "EXT_CONFIG_PATH=" in exported
    sha = exported.split("EXT_CONFIG_COMMIT=")[1].strip()
    assert len(sha) == 40


def test_binding_helper_reads_the_commit_or_abstains(tmp_path, monkeypatch):
    (tmp_path / SENTINEL).write_text("key: x\n")
    monkeypatch.setenv("EXT_CONFIG_PATH", str(tmp_path))
    monkeypatch.setenv("EXT_CONFIG_PINNED", "true")
    monkeypatch.setenv("EXT_CONFIG_COMMIT", "feedface")
    assert bound_checkout() == (tmp_path.resolve(), "feedface")
    monkeypatch.delenv("EXT_CONFIG_PATH")
    monkeypatch.delenv("EXT_CONFIG_REQUIRED", raising=False)
    with pytest.raises(pytest.skip.Exception):
        bound_checkout()
    monkeypatch.setenv("EXT_CONFIG_REQUIRED", "true")
    with pytest.raises(pytest.fail.Exception):
        bound_checkout()


def test_the_workflow_checkout_is_shallow_and_ignored():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    checkout = next(
        s for s in jobs["offline"]["steps"] if (s.get("with") or {}).get("path") == "ext-config"
    )
    assert checkout["with"]["fetch-depth"] == 1
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "ext-config/x.yaml"], cwd=ROOT, check=False
    )
    assert ignored.returncode == 0
    assert external_config.DEFAULT_CHECKOUT == checkout["with"]["path"]


def test_the_report_environment_panel_records_the_full_commit():
    from reporting.allure_environment import environment_rows

    rows = environment_rows(
        env="qa",
        base_url="",
        browsers=[],
        headed=False,
        auth_mode="none",
        run_id="r",
        environ={"EXT_CONFIG_COMMIT": "0123456789abcdef0123"},
    )
    assert rows["External.Config.Commit"] == "0123456789abcdef0123"
