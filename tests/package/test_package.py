"""The package installs, imports and exposes its console script."""

from __future__ import annotations

from importlib.metadata import entry_points, version
from pathlib import Path

import aurseal

ROOT = Path(__file__).resolve().parents[2]


def test_the_installed_package_is_the_src_tree():
    # An editable install resolves to src/aurseal, never to a root-level directory.
    assert Path(aurseal.__file__).resolve().parent.parts[-2:] == ("src", "aurseal")


def test_the_version_comes_from_the_installed_metadata():
    assert aurseal.__version__ == version("aurseal")


def test_the_console_script_points_at_the_cli_app():
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts.get("aurseal") == "aurseal.cli:app"


def test_the_package_is_typed():
    assert (ROOT / "src" / "aurseal" / "py.typed").is_file()
