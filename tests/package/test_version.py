"""The version is written once, in src/aurseal/__init__.py, and everything else derives it."""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

import tomllib
from typer.testing import CliRunner

import aurseal
from aurseal.assignment.scaffold import TEMPLATE_TAG
from aurseal.cli import app

ROOT = Path(__file__).resolve().parents[2]
# A release tag: aurseal-v + a PEP 440 public version without epoch or local part.
TAG_RE = re.compile(r"^aurseal-v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?)$")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_reads_the_version_from_the_package():
    data = _pyproject()
    assert "version" not in data["project"]
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["hatch"]["version"]["path"] == "src/aurseal/__init__.py"


def test_the_source_literal_is_the_installed_version():
    # Fails when the editable install is stale: reinstall after a version bump.
    assert aurseal.__version__ == version("aurseal")


def test_the_version_flag_and_command_print_the_same_version():
    runner = CliRunner()
    flag = runner.invoke(app, ["--version"])
    command = runner.invoke(app, ["version"])
    assert flag.exit_code == 0 and command.exit_code == 0
    assert flag.output.strip() == command.output.strip() == aurseal.__version__


def test_the_release_tag_is_aurseal_v_plus_the_version():
    match = TAG_RE.match(TEMPLATE_TAG)
    assert match, TEMPLATE_TAG
    assert match.group(1) == aurseal.__version__


def test_the_tag_pattern_is_not_vacuous():
    assert not TAG_RE.match(f"v{aurseal.__version__}")
    assert not TAG_RE.match("aurseal-v1.2")
    assert TAG_RE.match("aurseal-v1.2.0rc1")
