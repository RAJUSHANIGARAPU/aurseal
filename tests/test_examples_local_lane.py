"""Every example has a local lane, the pull-request workflow runs all of them, and nothing in
it points at a public host or an unpinned image. Why: docs/ci-decisions.md#examples-lane

Parse checks only: no Docker. ``make local-env`` runs where GNU make is installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted(p for p in (ROOT / "examples").iterdir() if (p / ".scaffold-manifest").is_file())
WORKFLOW = ROOT / ".github" / "workflows" / "examples-pr.yml"
_LOCAL_URL = re.compile(
    r"^[A-Z_]+=https?://(localhost|127\.0\.0\.1|[a-z][a-z0-9-]*)(:[0-9]+)?(/.*)?$"
)
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
_ids = [p.name for p in EXAMPLES]


def _compose(example: Path) -> dict:
    return yaml.safe_load((example / "docker-compose.local.yml").read_text(encoding="utf-8"))


def test_there_are_examples_to_check():
    assert len(EXAMPLES) >= 6


def test_the_pull_request_workflow_runs_every_example():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    matrix = [row["example"] for row in jobs["local"]["strategy"]["matrix"]["include"]]
    assert sorted(matrix) == _ids


@pytest.mark.parametrize("example", EXAMPLES, ids=_ids)
def test_the_local_lane_is_outside_the_template_owned_files(example: Path):
    owned = {
        line.strip()
        for line in (example / ".scaffold-manifest").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    gnumakefile = (example / "GNUmakefile").read_text(encoding="utf-8")
    assert re.search(r"^test-local:", gnumakefile, re.M)
    assert re.search(r"^local-env:", gnumakefile, re.M)
    assert {"GNUmakefile", "docker-compose.local.yml"}.isdisjoint(owned)


@pytest.mark.parametrize("example", EXAMPLES, ids=_ids)
def test_local_targets_are_pinned_and_bound_to_loopback(example: Path):
    for name, service in _compose(example)["services"].items():
        if "image" in service:
            assert _DIGEST.search(service["image"]), f"{name}: image not pinned by digest"
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port} is open beyond loopback"
    for dockerfile in (example / "local").rglob("Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8")
        for image in re.findall(r"^FROM\s+(\S+)", text, re.M):
            assert _DIGEST.search(image), f"{dockerfile}: {image} not pinned by digest"
        for source in re.findall(r"^ADD\s+.*?(https://\S+)", text, re.M):
            assert re.search(r"#[0-9a-f]{40}$", source), f"{dockerfile}: {source} not a commit"


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize("example", EXAMPLES, ids=_ids)
def test_local_env_names_only_local_hosts(example: Path):
    out = subprocess.run(
        ["make", "-s", "local-env"], cwd=example, capture_output=True, text=True, check=True
    ).stdout.split()
    assert out, "local-env printed nothing"
    assert [pair for pair in out if not _LOCAL_URL.match(pair)] == []


def test_the_local_url_check_refuses_a_public_host():
    """Positive control for the pattern the two tests above and the workflow rely on."""
    assert _LOCAL_URL.match("APP_BASE_URL=http://localhost:8301")
    assert _LOCAL_URL.match("TARGET_URL=http://quickpizza:3333")
    assert not _LOCAL_URL.match("APP_BASE_URL=https://www.example.test")
    assert not _LOCAL_URL.match("APP_API_BASE_URL=http://10.0.0.5.example.test:80/")
