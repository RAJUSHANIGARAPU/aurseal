"""Lint for path-scoped assistant rules (``.claude/rules/*.md``).

A rules file holds a topic note that matters only when certain files are touched, so the
project guide stays short and the detail loads when relevant. Each file starts with YAML
front matter naming the paths it applies to::

    ---
    paths:
      - "ui/pages/**"
      - "ui/tests/**"
    ---
    Note body...

Findings: no front matter; ``paths`` missing, empty or not a list of strings; a glob that
matches no tracked file (a rule that applies to nothing); a rules file the guide
(``CLAUDE.md``) does not name.

``python -m tools.rules_lint [--dir .claude/rules] [--guide CLAUDE.md]``: exit 0 clean,
1 findings.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def glob_regex(pattern: str) -> re.Pattern[str]:
    """``**`` spans directories, ``*`` and ``?`` stay inside one path segment."""
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        elif pattern[index] == "?":
            out, index = out + "[^/]", index + 1
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return re.compile(out + r"\Z")


def tracked_files(root: str | Path = ROOT) -> list[str]:
    done = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def front_matter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    data = yaml.safe_load(text[4:end]) or {}
    return data if isinstance(data, dict) else None


def lint_rules(
    directory: str | Path, files: Iterable[str], guide_text: str | None = None
) -> list[str]:
    findings: list[str] = []
    tracked = list(files)
    for path in sorted(Path(directory).glob("*.md")):
        meta = front_matter(path.read_text(encoding="utf-8"))
        if meta is None:
            findings.append(f"{path.name}: no YAML front matter")
            continue
        paths = meta.get("paths")
        if not isinstance(paths, list) or not paths:
            findings.append(f"{path.name}: front matter needs a non-empty 'paths' list")
            continue
        for pattern in paths:
            if not isinstance(pattern, str) or not pattern.strip():
                findings.append(f"{path.name}: path {pattern!r} is not a glob string")
                continue
            regex = glob_regex(pattern.strip())
            if not any(regex.match(name) for name in tracked):
                findings.append(f"{path.name}: {pattern!r} matches no tracked file")
        if guide_text is not None and path.name not in guide_text:
            findings.append(f"{path.name}: not named in the project guide")
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.rules_lint")
    parser.add_argument("--dir", default=str(ROOT / ".claude" / "rules"))
    parser.add_argument("--guide", default=str(ROOT / "CLAUDE.md"))
    args = parser.parse_args(argv)
    guide = Path(args.guide)
    findings = lint_rules(
        args.dir,
        tracked_files(),
        guide.read_text(encoding="utf-8") if guide.is_file() else None,
    )
    for finding in findings:
        print(finding)
    if not findings:
        print(f"rules lint: {args.dir} clean")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
