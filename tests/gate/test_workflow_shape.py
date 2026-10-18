"""Shape rules of gate.workflow_lint: concurrency, time budget, retention, pinned browser
container, nightly switch and the secret scan - each proven able to fail, and the
repository's own workflows proven to pass."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from gate.nightly import SCHEDULE_SKIPPED, skipped_on_schedule, summary_line
from gate.nightly import main as nightly_main
from gate.workflow_lint import (
    ShapeOptions,
    critical_path,
    image_playwright_version,
    lint_workflow,
    lint_workflows,
    playwright_pin,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

BASE = """
name: CI
on:
  push:
    branches: [main]
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  a:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: pytest tests/
"""


def _problems(tmp_path: Path, body: str, requirements: str = "playwright==1.58.0\n", **kw):
    req = tmp_path / "requirements.txt"
    req.write_text(requirements)
    path = tmp_path / "ci.yml"
    path.write_text(textwrap.dedent(body))
    shape = ShapeOptions(requirements=req, **kw)
    return [f"{f.where}: {f.problem}" for f in lint_workflow(path, shape=shape)]


def _job(body: str) -> str:
    return BASE + textwrap.indent(textwrap.dedent(body), "  ")


def test_base_fixture_is_clean(tmp_path):
    assert _problems(tmp_path, BASE) == []


# ------------------------------------------------------------------ time budget


def test_a_job_without_timeout_is_named(tmp_path):
    body = _job(
        """
        b:
          runs-on: ubuntu-latest
          steps:
            - run: echo hi
        """
    )
    assert _problems(tmp_path, body) == ["jobs.b: has no timeout-minutes"]


def test_critical_path_sums_a_chain_and_not_parallel_siblings(tmp_path):
    jobs = {
        "A": {"timeout-minutes": 10},
        "B": {"timeout-minutes": 30, "needs": "A"},
        "C": {"timeout-minutes": 30, "needs": ["B"]},
        "D": {"timeout-minutes": 25, "needs": ["A"]},
    }
    assert critical_path(jobs) == (["A", "B", "C"], 70)
    siblings = {"A": {"timeout-minutes": 10}, "X": {"timeout-minutes": 30, "needs": "A"}}
    siblings["Y"] = {"timeout-minutes": 30, "needs": "A"}
    assert critical_path(siblings)[1] == 40


def test_budget_finding_lists_the_path(tmp_path):
    body = """
    name: CI
    on: [push]
    concurrency: ci
    jobs:
      A:
        runs-on: ubuntu-latest
        timeout-minutes: 10
        steps: [{run: echo}]
      B:
        needs: A
        runs-on: ubuntu-latest
        timeout-minutes: 30
        steps: [{run: echo}]
      C:
        needs: B
        runs-on: ubuntu-latest
        timeout-minutes: 30
        steps: [{run: echo}]
    """
    problems = _problems(tmp_path, body, run_budget_minutes=60)
    assert problems == ["workflow: critical path A, B, C = 70 min exceeds the run budget of 60 min"]


# ------------------------------------------------------------------ concurrency


def test_concurrency_rules(tmp_path):
    no_group = BASE.replace(
        "concurrency:\n  group: ${{ github.workflow }}-${{ github.ref }}\n"
        "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n",
        "",
    )
    assert "workflow: has no top-level concurrency group" in _problems(tmp_path, no_group)
    always = BASE.replace("${{ github.event_name == 'pull_request' }}", "true")
    assert any("cancels in-progress" in p for p in _problems(tmp_path, always))
    other = BASE.replace("${{ github.event_name == 'pull_request' }}", "${{ always() }}")
    assert any("must be false on the default branch" in p for p in _problems(tmp_path, other))
    assert not _problems(
        tmp_path, BASE.replace("${{ github.event_name == 'pull_request' }}", "false")
    )


# ------------------------------------------------------------------ retention


def _upload(path: str, days: int | None) -> str:
    retention = "" if days is None else f"\n        retention-days: {days}"
    job = f"""
up:
  runs-on: ubuntu-latest
  timeout-minutes: 5
  steps:
    - uses: actions/upload-artifact@v4
      with:
        name: x
        path: {path}{retention}
"""
    return BASE + textwrap.indent(job, "  ")


def test_heavy_upload_retention(tmp_path):
    assert any("heavy artefact" in p for p in _problems(tmp_path, _upload("reports/traces/", 30)))
    assert _problems(tmp_path, _upload("reports/traces/", 7)) == []
    assert any("without retention-days" in p for p in _problems(tmp_path, _upload("x/", None)))
    assert _problems(tmp_path, _upload("baseline/", 30)) == []
    assert any("limit 30" in p for p in _problems(tmp_path, _upload("baseline/", 90)))


# ------------------------------------------------------------------ browser container


def _container(image: str, options: str | None = "--ipc=host", step_if: str | None = None):
    opts = f"\n            options: {options}" if options else ""
    cond = f"\n              if: {step_if}" if step_if else ""
    return _job(
        f"""
        visual:
          runs-on: ubuntu-latest
          timeout-minutes: 10
          container:
            image: {image}{opts}
          steps:
            - run: pytest tests/visual{cond}
        """
    )


GOOD_IMAGE = "mcr.microsoft.com/playwright/python:v1.58.0-jammy"


def test_pinned_container_matches_the_requirements_pin(tmp_path):
    assert _problems(tmp_path, _container(GOOD_IMAGE)) == []
    mismatch = _problems(tmp_path, _container(GOOD_IMAGE), requirements="playwright==1.57.0\n")
    assert mismatch == [
        "jobs.visual: image Playwright 1.58.0 differs from requirements playwright==1.57.0"
    ]
    latest = _problems(tmp_path, _container("mcr.microsoft.com/playwright/python:latest"))
    assert any("not pinned" in p for p in latest)


def test_browser_container_needs_shared_memory(tmp_path):
    assert any("--ipc=host" in p for p in _problems(tmp_path, _container(GOOD_IMAGE, None)))
    assert _problems(tmp_path, _container(GOOD_IMAGE, "--shm-size=2g")) == []
    assert _problems(tmp_path, _container(GOOD_IMAGE, "--shm-size=512m"))


def test_branch_gating_belongs_on_the_job(tmp_path):
    body = _container(GOOD_IMAGE, step_if="github.ref == 'refs/heads/main'")
    assert any("gate the job instead" in p for p in _problems(tmp_path, body))


def test_version_helpers(tmp_path):
    assert image_playwright_version(GOOD_IMAGE) == "1.58.0"
    assert image_playwright_version("mcr.microsoft.com/playwright/python:latest") is None
    req = tmp_path / "r.txt"
    req.write_text("pytest==9\nplaywright==1.58.0  # browsers\n")
    assert playwright_pin(req) == "1.58.0"


# ------------------------------------------------------------------ nightly


def test_cron_on_the_hour_is_a_finding(tmp_path):
    body = BASE.replace(
        "on:\n  push:\n    branches: [main]\n",
        'on:\n  schedule:\n    - cron: "0 2 * * *"\n',
    )
    body = body.replace(
        "    timeout-minutes: 10\n", "    timeout-minutes: 10\n    if: vars.NIGHTLY_ENABLED\n"
    )
    assert "on.schedule: cron '0 2 * * *' fires on the hour" in _problems(tmp_path, body)
    assert _problems(tmp_path, body.replace('"0 2', '"17 2')) == []


def test_scheduled_workflow_jobs_must_read_the_switch(tmp_path):
    body = BASE.replace(
        "on:\n  push:\n    branches: [main]\n", 'on:\n  schedule:\n    - cron: "17 2 * * *"\n'
    )
    assert _problems(tmp_path, body) == [
        "jobs.a: is not guarded by the vars.NIGHTLY_ENABLED nightly switch"
    ]


def test_nightly_summary_line():
    assert "NIGHTLY_ENABLED is false" in summary_line(False, {"x": "y"})
    line = summary_line(True, skipped_on_schedule(token_present=False))
    assert "render (needs a bearer token" in line and "visual-baselines (manual only" in line
    assert "render" not in summary_line(True, skipped_on_schedule(token_present=True))


def test_nightly_cli_writes_the_line_and_outputs(tmp_path):
    summary, output = tmp_path / "s.md", tmp_path / "o.txt"
    env = {"NIGHTLY_ENABLED": "false"}
    args = ["summary", "--summary", str(summary), "--github-output", str(output)]
    assert nightly_main(args, env) == 0
    assert "disabled" in summary.read_text()
    assert "enabled=false" in output.read_text() and "render_token=false" in output.read_text()


def test_every_job_named_as_never_scheduled_exists():
    jobs = yaml.safe_load((WORKFLOWS / "tests.yml").read_text())["jobs"]
    assert set(SCHEDULE_SKIPPED) | {"render"} <= set(jobs)


# ------------------------------------------------------------------ secret scan


SCAN = """
name: Secrets
on: [push]
concurrency: scan
jobs:
  secret-scan:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: gitleaks git . --config .gitleaks.toml --redact --no-banner
"""


def test_secret_scan_job_clean_fixture(tmp_path):
    assert _problems(tmp_path, SCAN) == []


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        (
            "    runs-on: ubuntu-latest\n    timeout",
            "    if: \"!startsWith(github.ref, 'refs/heads/bringup/')\"\n"
            "    runs-on: ubuntu-latest\n    timeout",
            "secret scan job has an if: condition",
        ),
        ("--no-banner", "--no-banner || true", "secret scan masks its exit status"),
        (
            "    timeout-minutes: 10\n",
            "    timeout-minutes: 10\n    continue-on-error: true\n",
            "secret scan job has continue-on-error",
        ),
        ("fetch-depth: 0", "fetch-depth: 1", "secret scan checkout must use fetch-depth: 0"),
        (
            "        with:\n          fetch-depth: 0\n",
            "",
            "secret scan checkout must use fetch-depth: 0",
        ),
        (
            "    timeout-minutes: 10\n",
            "    timeout-minutes: 10\n    needs: other\n",
            "secret scan job has needs:",
        ),
    ],
)
def test_secret_scan_rules_can_fail(tmp_path, old, new, problem):
    assert old in SCAN
    assert any(problem in p for p in _problems(tmp_path, SCAN.replace(old, new)))


def test_a_workflow_directory_without_a_secret_scan_is_a_finding(tmp_path):
    (tmp_path / "ci.yml").write_text(textwrap.dedent(BASE))
    findings = lint_workflows(tmp_path, shape=ShapeOptions(requirements=tmp_path / "none"))
    assert [f.problem for f in findings] == ["no job runs the secret scan"]


# ------------------------------------------------------------------ the real workflows


def test_the_repository_workflows_pass_every_shape_rule():
    findings = lint_workflows(WORKFLOWS)
    assert findings == [], "\n".join(map(str, findings))


def test_triggers_and_feature_branch_policy():
    tests = yaml.safe_load((WORKFLOWS / "tests.yml").read_text())
    on = tests.get("on", tests.get(True))
    assert on["push"]["branches"] == ["main", "bringup/**"]
    assert on["pull_request"]["branches"] == ["main"]
    assert "workflow_dispatch" in on and on["schedule"]
    scan = yaml.safe_load((WORKFLOWS / "secret-scan.yml").read_text())
    scan_on = scan.get("on", scan.get(True))
    assert scan_on["push"]["branches"] == ["**"] and "pull_request" in scan_on
    assert "gitleaks" not in (WORKFLOWS / "tests.yml").read_text()


GITLEAKS = shutil.which("gitleaks")


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks is not installed on this machine")
def test_the_scanner_finds_an_invented_key_and_honours_the_allow_list(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Assembled at run time so this file itself never holds a key-shaped string.
    fake = "AKIA" + "Z7Q3XW5PLM2NB4VC"
    (repo / "settings.py").write_text(f'AWS_KEY = "{fake}"\n')
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "x"], check=True)
    config = tmp_path / "gitleaks.toml"
    config.write_text("[extend]\nuseDefault = true\n")
    scan = [GITLEAKS, "git", str(repo), "--redact", "--no-banner", "--config", str(config)]
    assert subprocess.run(scan, capture_output=True, check=False).returncode != 0
    config.write_text(
        '[extend]\nuseDefault = true\n\n[[allowlists]]\nregexTarget = "secret"\n'
        f"regexes = ['''^{fake}$''']\n"
    )
    assert subprocess.run(scan, capture_output=True, check=False).returncode == 0
