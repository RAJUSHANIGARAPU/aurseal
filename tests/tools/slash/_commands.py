"""Reading the slash command files the way the client does: front matter, then the body."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ROOT / ".claude" / "commands"


def read_command(name: str) -> tuple[dict, str]:
    text = (COMMANDS / f"{name}.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name}.md has no front matter"
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


def claude_md_lists(name: str) -> bool:
    return f"`/{name}" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
