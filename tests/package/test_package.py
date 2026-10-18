"""The package installs, imports and exposes its console script."""

from __future__ import annotations

from importlib.metadata import entry_points, version
from pathlib import Path

import testkeel

ROOT = Path(__file__).resolve().parents[2]


def test_the_installed_package_is_the_src_tree():
    # An editable install resolves to src/testkeel, never to a root-level directory.
    assert Path(testkeel.__file__).resolve().parent.parts[-2:] == ("src", "testkeel")


def test_the_version_comes_from_the_installed_metadata():
    assert testkeel.__version__ == version("testkeel")


def test_the_console_script_points_at_the_cli_app():
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts.get("testkeel") == "testkeel.cli:app"


def test_the_package_is_typed():
    assert (ROOT / "src" / "testkeel" / "py.typed").is_file()
