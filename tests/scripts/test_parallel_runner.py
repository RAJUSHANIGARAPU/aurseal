"""The local parallel runner and its container: same image and selection as CI, bash 3.2 safe."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from core.ci_selection import parse_pytest_invocations
from gate import parallel_run
from gate.workflow_lint import image_playwright_version, playwright_pin

ROOT = Path(__file__).resolve().parents[2]
CONTAINER = ROOT / "scripts" / "run-in-container.sh"
RUNNER = ROOT / "scripts" / "run-parallel.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
_IMAGE = re.compile(r"mcr\.microsoft\.com/playwright/python:v[0-9.]+-[a-z]+")


def _script_image() -> str:
    found = set(_IMAGE.findall(CONTAINER.read_text(encoding="utf-8")))
    assert len(found) == 1, f"expected one image tag in {CONTAINER.name}, found {found}"
    return found.pop()


def _workflow_images() -> set[str]:
    images = set()
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (data.get("jobs") or {}).values():
            container = job.get("container") if isinstance(job, dict) else None
            image = container if isinstance(container, str) else (container or {}).get("image")
            if image and "playwright/python" in image:
                images.add(image)
    return images


class TestContainerImage:
    def test_the_tag_equals_the_dockerfile_one(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(rf"^FROM {re.escape(_script_image())}\s*$", dockerfile, re.M)

    def test_the_tag_equals_every_ci_container_image(self):
        images = _workflow_images()
        assert images, "the control proves nothing if CI runs no Playwright image"
        assert images == {_script_image()}

    def test_the_tag_matches_the_playwright_pin(self):
        version = image_playwright_version(_script_image())
        assert version == playwright_pin(ROOT / "requirements.txt")

    def test_docker_is_checked_before_anything_runs(self):
        text = CONTAINER.read_text(encoding="utf-8")
        assert text.index("docker info") < text.index("docker run")

    def test_one_named_volume_holds_leases_and_the_token_cache(self):
        text = CONTAINER.read_text(encoding="utf-8")
        assert '-v "$CACHE_VOLUME":/cache' in text
        assert "USER_POOL_LEASE_DIR=/cache/leases" in text
        assert "TEST_AUTH_CACHE_DIR=/cache/auth" in text

    def test_headed_on_a_virtual_display(self):
        assert 'xvfb-run -a --server-args="-screen 0 1920x1080x24"' in CONTAINER.read_text()

    def test_the_venv_is_built_without_ensurepip_and_heals(self):
        """The image's Python has no ensurepip; a venv that cannot run pip is rebuilt."""
        text = CONTAINER.read_text(encoding="utf-8")
        assert "python3 -m venv --system-site-packages --without-pip /venv" in text
        assert "/venv/bin/python -m pip install" in text and "/venv/bin/pip " not in text
        heal = text.index("if ! /venv/bin/python -m pip --version")
        assert heal < text.index("find /venv -mindepth 1 -delete") < text.index("-m venv")
        # the marker is written only after the install succeeded (set -e stops before it)
        assert text.index("-m pip install") < text.index("> /venv/.requirements.sha256")

    def test_an_init_process_reaps_the_display(self):
        """Without --init, xvfb-run as PID 1 can hang after the command has finished."""
        assert "docker run --rm --init" in CONTAINER.read_text(encoding="utf-8")

    def test_no_dotted_variable_names_are_passed(self):
        """xvfb-run starts the command through /bin/sh, which drops names with a dot."""
        passed = re.findall(r"-e ([A-Za-z0-9_.]+)", CONTAINER.read_text(encoding="utf-8"))
        assert passed and not [name for name in passed if "." in name]


class TestScripts:
    @pytest.mark.parametrize("script", [CONTAINER, RUNNER], ids=lambda p: p.name)
    def test_parses(self, script):
        assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0

    @pytest.mark.parametrize("script", [CONTAINER, RUNNER], ids=lambda p: p.name)
    def test_is_executable(self, script):
        assert os.access(script, os.X_OK)

    @pytest.mark.parametrize("script", [CONTAINER, RUNNER], ids=lambda p: p.name)
    def test_no_bash_4_features(self, script):
        """macOS ships bash 3.2; --on-host must run on it."""
        code = "\n".join(
            line for line in script.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )  # fmt: skip
        for pattern in (
            r"declare -A",
            r"\bmapfile\b",
            r"\breadarray\b",
            r"\$\{\w+(,,|\^\^)",
            r"\bwait -n\b",
            r"&>>",
            r"\|&",
            r'(?<!\+)"\$\{\w+\[@\]\}"',  # unguarded: fails on an empty array under set -u
        ):
            assert not re.search(pattern, code), f"{script.name}: {pattern}"

    def test_unknown_option_is_a_usage_error(self):
        result = subprocess.run(
            ["bash", str(RUNNER), "--bogus"], capture_output=True, text=True, check=False
        )
        assert result.returncode == 2 and "unknown option --bogus" in result.stderr


def _ci_gate_selection() -> str:
    """The -m expression of the browser job's gate command in the CI workflow."""
    gate = [
        inv
        for inv in parse_pytest_invocations(WORKFLOW)
        if inv.step == "Run Smoke Tests" and "--junitxml=reports/junit-browser.xml" in inv.args
    ]
    assert len(gate) == 1, gate
    args = list(gate[0].args)
    return args[args.index("-m") + 1]


class TestSelection:
    def test_the_runner_selects_what_ci_selects(self):
        result = subprocess.run(
            ["bash", str(RUNNER), "--print-selection"],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
            env={**os.environ, "PY": sys.executable},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == _ci_gate_selection()

    def test_selection_is_the_gate_command(self):
        assert parallel_run.selection() == _ci_gate_selection()

    def test_every_shard_runs_the_serial_lane_split(self):
        text = RUNNER.read_text(encoding="utf-8")
        assert "--serial-lane" in text and '"serial/$N"' in text
        assert "-p gate.pytest_shard" in text and "gate.merge" in text and "--summary" in text


class TestFloorAndLeases:
    @pytest.mark.parametrize(
        "total, workers, serial, expected",
        [
            (10, 3, False, 4),
            (10, 1, False, 10),
            (10, 4, True, 0),
            (0, 3, False, 0),
            (1, 4, False, 1),
        ],
    )
    def test_floor_is_split_and_rounded_up(self, total, workers, serial, expected):
        assert parallel_run.shard_floor(total, workers, serial) == expected

    def test_no_workers_is_an_error(self):
        with pytest.raises(ValueError):
            parallel_run.shard_floor(10, 0)

    def test_total_floor_follows_run_trust(self):
        assert parallel_run.total_floor({"RUN_TRUST_MIN_EXECUTED": "25"}) == 25
        assert parallel_run.total_floor({}) == 10

    def test_leases_name_each_shard_and_count_shards_per_user(self, tmp_path):
        files = []
        for shard, index in (("1", 1), ("2", 2), ("serial", 1)):
            path = tmp_path / f"shard-{shard}.lease.json"
            path.write_text(json.dumps({"index": index, "username": f"user{index}@example.test"}))
            files.append(path)
        files.append(tmp_path / "shard-3.lease.json")  # never written: no pool user
        out = io.StringIO()
        assert parallel_run.main(["leases", *map(str, files)], out) == 0
        lines = out.getvalue().splitlines()
        assert "shard-1: pool user 1 (user1@example.test)" in lines
        assert lines[3].startswith("shard-3: no pool user")
        assert "user1@example.test served 2 shard(s)" in lines
        assert "user2@example.test served 1 shard(s)" in lines

    def test_cli_floor(self, monkeypatch):
        monkeypatch.setenv("RUN_TRUST_MIN_EXECUTED", "9")
        out = io.StringIO()
        assert parallel_run.main(["floor", "--workers", "2"], out) == 0
        assert out.getvalue().strip() == "5"
