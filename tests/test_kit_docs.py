"""The kit's CLI reference (docs/kit/cli.md) names exactly the commands and options the CLI has.

Skipped where the package is not installed; the offline CI job installs it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("aurseal", reason="aurseal is not installed")
typer_main = pytest.importorskip("typer.main")

from aurseal.cli import app  # noqa: E402

CLI_DOC = Path(__file__).resolve().parents[1] / "docs" / "kit" / "cli.md"


def _commands(group, prefix: str = "aurseal") -> dict[str, set[str]]:
    """``{"aurseal new-assignment": {"--type", ...}}`` for every leaf command."""
    found: dict[str, set[str]] = {}
    for name, command in group.commands.items():
        path = f"{prefix} {name}"
        if hasattr(command, "commands"):
            found.update(_commands(command, path))
            continue
        options = set()
        for param in command.params:
            if getattr(param, "param_type_name", "") == "option":
                options.update(param.opts)
                options.update(param.secondary_opts)
        found[path] = options - {"--help"}
    return found


def _documented() -> dict[str, set[str]]:
    sections = re.split(
        r"^## `(aurseal[^`]*)`\s*$", CLI_DOC.read_text(encoding="utf-8"), flags=re.M
    )
    documented = {}
    for heading, body in zip(sections[1::2], sections[2::2], strict=True):
        rows = [line for line in body.splitlines() if line.startswith("| `-")]
        options = set()
        for row in rows:
            first_cell = row.split("|")[1]
            options.update(re.findall(r"`(--?[a-z][a-z-]*)`", first_cell))
        documented[heading] = options
    return documented


def test_the_cli_reference_documents_every_command_and_option():
    actual = _commands(typer_main.get_command(app))
    documented = _documented()
    assert sorted(documented) == sorted(actual)
    for command, options in actual.items():
        assert documented[command] == options, command


def test_neither_side_is_vacuous():
    actual = _commands(typer_main.get_command(app))
    documented = _documented()
    assert {"--type", "--dry-run", "--no-dry-run"} <= actual["aurseal new-assignment"]
    assert {"--output", "-o"} <= documented["aurseal report"]
