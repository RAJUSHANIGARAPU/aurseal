"""The local container runner (docker-compose.yml) and its pinned image.

The parse checks need no Docker; ``docker compose config`` is skipped without the CLI, and
the build-and-collect smoke runs only with ``COMPOSE_SMOKE=true`` (it builds the image).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from gate.workflow_lint import check_playwright_image, playwright_pin

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
_SECRETISH = re.compile(r"key|token|password|secret", re.IGNORECASE)
_REFERENCE = re.compile(r"^\$\{[A-Z0-9_]+(:-[^}]*)?\}$")


def test_no_obsolete_version_key():
    assert "version" not in COMPOSE


def test_tests_service_mounts_reports_and_passes_the_settings_through():
    tests = SERVICES["tests"]
    assert "./reports:/app/reports" in tests["volumes"]
    env = tests["environment"]
    assert env["TEST_ENV"] == "${TEST_ENV:-qa}" and env["BROWSER"] == "${BROWSER:-chromium}"
    assert env["HEADLESS"] == "true"


def test_optional_services_are_profile_gated():
    assert SERVICES["report"]["profiles"] == ["report"]
    assert SERVICES["demo"]["profiles"] == ["demo"]
    assert "healthcheck" in SERVICES["demo"]
    assert SERVICES["tests"]["depends_on"]["demo"]["required"] is False


def test_every_image_is_pinned_to_an_exact_tag():
    for name, service in SERVICES.items():
        image = service.get("image")
        if image is None:
            continue
        tag = image.rsplit(":", 1)[-1] if ":" in image else ""
        assert tag and tag != "latest" and re.fullmatch(r"v?\d+(\.\d+)+.*", tag), name


def test_no_literal_secret_values():
    for name, service in SERVICES.items():
        for key, value in (service.get("environment") or {}).items():
            if _SECRETISH.search(key):
                assert _REFERENCE.match(str(value)), f"{name}.{key} must be a ${{VAR}} reference"


def test_the_dockerfile_image_matches_the_requirements_pin():
    first = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()[0]
    image = first.split()[1]
    assert check_playwright_image(image, playwright_pin(ROOT / "requirements.txt")) == []


DOCKER = shutil.which("docker")


@pytest.mark.skipif(DOCKER is None, reason="the docker CLI is not installed")
def test_docker_compose_config_validates():
    done = subprocess.run(
        [DOCKER, "compose", "--profile", "demo", "--profile", "report", "config", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 and "is not a docker command" in done.stderr:
        pytest.skip("docker compose plugin is not installed")
    assert done.returncode == 0, done.stderr


@pytest.mark.slow
@pytest.mark.skipif(
    DOCKER is None or os.environ.get("COMPOSE_SMOKE", "").lower() != "true",
    reason="builds the image: opt in with COMPOSE_SMOKE=true on a machine with Docker",
)
def test_the_container_collects_the_suite():
    done = subprocess.run(
        [DOCKER, "compose", "run", "--rm", "tests", "tests/gate", "--co", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
