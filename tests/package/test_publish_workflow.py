"""The publish workflow: which pushes fire it, what it can publish, and its checks run for real.

Tag filters are evaluated the way GitHub evaluates them (``*`` matches anything but ``/``),
so the claim "a aurseal-v tag fires publish.yml and nothing else, a plain v tag never
fires it" is checked against every workflow in the repository, not assumed. The tag and
branch checks are taken from the workflow file and executed with bash, so what is tested
is what CI runs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from aurseal import __version__
from aurseal.assignment.scaffold import TEMPLATE_TAG
from gate.workflow_lint import DEFAULT_SHAPE, lint_workflow

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish.yml"
SHA_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
needs_bash_and_git = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(data: dict) -> object:
    return data.get("on", data.get(True))


def _glob(pattern: str) -> re.Pattern[str]:
    """A GitHub ref filter pattern: ``**`` matches anything, ``*`` anything but ``/``."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append(".?")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _matches(patterns: object, name: str) -> bool:
    items = [patterns] if isinstance(patterns, str) else list(patterns or [])
    return any(_glob(p).match(name) for p in items)


def fires_on_tag_push(data: dict, tag: str) -> bool:
    """Whether pushing ``tag`` starts this workflow, by GitHub's push-filter rules."""
    triggers = _triggers(data)
    if isinstance(triggers, str):
        return triggers == "push"
    if isinstance(triggers, list):
        return "push" in triggers
    if not isinstance(triggers, dict) or "push" not in triggers:
        return False
    push = triggers["push"] or {}
    if "tags" in push:
        return _matches(push["tags"], tag)
    if "tags-ignore" in push:
        return not _matches(push["tags-ignore"], tag)
    # A push filter on branches or paths only never fires for a tag; no filter fires for all.
    return not ({"branches", "branches-ignore", "paths", "paths-ignore"} & set(push))


def _step(job: str, name: str) -> dict:
    steps = _load(PUBLISH)["jobs"][job]["steps"]
    return next(s for s in steps if s.get("name") == name)


# ------------------------------------------------------------------------- triggers


def test_the_tag_filter_semantics_are_not_vacuous():
    assert fires_on_tag_push({"on": {"push": {"tags": ["v*"]}}}, "v1.0.0")
    assert not fires_on_tag_push({"on": {"push": {"tags": ["v*"]}}}, "aurseal-v1.0.0")
    assert not fires_on_tag_push({"on": {"push": {"branches": ["main"]}}}, "v1.0.0")
    assert fires_on_tag_push({"on": "push"}, "v1.0.0")
    assert fires_on_tag_push({"on": {"push": None}}, "v1.0.0")
    assert not fires_on_tag_push({"on": {"pull_request": None}}, "v1.0.0")
    assert not _glob("v*").match("v1/x")


def test_publish_fires_on_aurseal_tags_and_a_manual_run_only():
    triggers = _triggers(_load(PUBLISH))
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"tags": ["aurseal-v*"]}


def test_the_release_tag_of_this_version_fires_publish_and_no_other_workflow():
    fired = sorted(
        p.name for p in WORKFLOWS.glob("*.y*ml") if fires_on_tag_push(_load(p), TEMPLATE_TAG)
    )
    assert fired == ["publish.yml"]


def test_a_plain_v_tag_fires_release_and_never_publish():
    plain = f"v{__version__}"
    assert fires_on_tag_push(_load(WORKFLOWS / "release.yml"), plain)
    assert not fires_on_tag_push(_load(PUBLISH), plain)


# ----------------------------------------------------------------- publishing rights


def test_the_workflow_passes_the_repository_lint():
    assert lint_workflow(PUBLISH, shape=DEFAULT_SHAPE) == []


def test_only_the_publish_jobs_can_mint_an_upload_token():
    data = _load(PUBLISH)
    assert data["permissions"] == {"contents": "read"}
    minting = {
        name for name, job in data["jobs"].items()
        if (job.get("permissions") or {}).get("id-token") == "write"
    }  # fmt: skip
    assert minting == {"pypi", "testpypi"}
    assert data["jobs"]["build"].get("permissions") is None


def test_pypi_is_reached_from_a_tag_push_only_and_through_its_environment():
    pypi = _load(PUBLISH)["jobs"]["pypi"]
    assert pypi["needs"] == "build"
    assert pypi["environment"]["name"] == "pypi"
    assert "github.event_name == 'push'" in pypi["if"]
    assert "refs/tags/aurseal-v" in pypi["if"]
    publish = pypi["steps"][-1]
    assert publish["uses"].startswith("pypa/gh-action-pypi-publish@")
    assert "repository-url" not in (publish.get("with") or {})


def test_a_manual_run_publishes_to_testpypi_only():
    testpypi = _load(PUBLISH)["jobs"]["testpypi"]
    assert testpypi["if"] == "github.event_name == 'workflow_dispatch'"
    assert testpypi["environment"]["name"] == "testpypi"
    assert testpypi["steps"][-1]["with"]["repository-url"] == "https://test.pypi.org/legacy/"


def test_the_publish_action_is_pinned_to_a_commit():
    for job in ("pypi", "testpypi"):
        assert SHA_RE.match(_load(PUBLISH)["jobs"][job]["steps"][-1]["uses"]), job


def test_no_script_interpolates_an_expression():
    for job in _load(PUBLISH)["jobs"].values():
        for step in job["steps"]:
            assert "${{" not in str(step.get("run") or ""), step.get("name")


def test_build_and_package_share_the_wheel_check():
    command = "-m tools.wheel_check dist"
    assert command in _step("build", "The wheel contains only the package")["run"]
    package = _load(WORKFLOWS / "package.yml")["jobs"]["package"]["steps"]
    assert any(command in str(s.get("run") or "") for s in package)


def test_the_build_job_checks_builds_and_smoke_installs_in_order():
    names = [s.get("name") for s in _load(PUBLISH)["jobs"]["build"]["steps"]]
    order = [
        "The tagged commit is on the default branch",
        "The tag names the package version",
        "Build the wheel and sdist",
        "Check the distribution metadata",
        "The wheel contains only the package",
        "Smoke-install the wheel",
        "Keep the distribution",
    ]
    assert [n for n in names if n in order] == order
    smoke = _step("build", "Smoke-install the wheel")["run"]
    assert "aurseal-*.whl" in smoke and "list-types" in smoke


# ------------------------------------------------------------ the checks, run for real


def _run(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


@needs_bash_and_git
@pytest.mark.parametrize(
    ("tag", "ok"),
    [
        (TEMPLATE_TAG, True),
        (f"v{__version__}", False),
        ("aurseal-v99.0.0", False),
        (f"{TEMPLATE_TAG}.post1", False),
    ],
)
def test_the_tag_check_accepts_only_the_package_version(tag, ok):
    result = _run(_step("build", "The tag names the package version")["run"], ROOT, TAG=tag)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@needs_bash_and_git
def test_the_branch_check_rejects_a_commit_off_the_default_branch(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "one")
    on_main = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", on_main)
    _git(tmp_path, "checkout", "-q", "-b", "side")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "two")
    off_main = _git(tmp_path, "rev-parse", "HEAD")
    script = _step("build", "The tagged commit is on the default branch")["run"]

    assert _run(script, tmp_path, GITHUB_SHA=on_main, DEFAULT_BRANCH="main").returncode == 0
    rejected = _run(script, tmp_path, GITHUB_SHA=off_main, DEFAULT_BRANCH="main")
    assert rejected.returncode == 1
    assert "is not on main" in rejected.stderr
