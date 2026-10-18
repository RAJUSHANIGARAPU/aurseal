"""
The self-healing decisions made inside a time window, or why they cannot be counted.

The healing orchestrator appends one JSON line per decision to
``reports/ui_healing_sessions.jsonl``. An ordinary pytest run does not heal and
writes nothing there, so "no entries" usually means "not measured", not "zero
heals". ``read_window`` therefore returns either the entries in the window or a
reason the window could not be measured; it never turns a missing log into 0.

    python -m tools.slash.healing --since 2026-10-01T09:00:00Z
    python -m tools.slash.healing --fork-point main     # entries newer than the branch's fork point
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG = Path("reports/ui_healing_sessions.jsonl")


def parse_time(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class HealingWindow:
    measured: bool
    reason: str = ""
    entries: list[dict] = field(default_factory=list)
    skipped_lines: int = 0

    @property
    def applied(self) -> list[dict]:
        return [e for e in self.entries if e.get("applied") is True]


def read_window(log: str | Path, since: datetime, *, inclusive: bool = True) -> HealingWindow:
    """Entries at (``inclusive``) or strictly after ``since``."""
    path = Path(log)
    if not path.exists():
        return HealingWindow(
            False, f"no healing log at {path} (only the healing orchestrator writes it)"
        )
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return HealingWindow(False, f"the healing log {path} is empty")
    entries, skipped = [], 0
    for line in lines:
        try:
            entry = json.loads(line)
            stamp = parse_time(str(entry["timestamp"]))
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        if stamp > since or (inclusive and stamp == since):
            entries.append(entry)
    if not entries:
        return HealingWindow(
            False, f"no healing entries since {since.isoformat()}", skipped_lines=skipped
        )
    return HealingWindow(True, "", entries, skipped)


def fork_point_time(target: str, cwd: str | Path | None = None) -> datetime:
    """Commit time of the point where the current branch left ``target``."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    base = ""
    for ref in (f"origin/{target}", target):
        try:
            base = git("merge-base", "HEAD", ref)
            break
        except subprocess.CalledProcessError:
            continue
    if not base:
        raise ValueError(f"no common ancestor with {target}")
    return parse_time(git("show", "-s", "--format=%cI", base))


def describe(window: HealingWindow, limit: int = 5, *, verbose: bool = False) -> str:
    if not window.measured:
        return f"Healing: not measured - {window.reason}"
    applied = window.applied
    lines = [f"Healing: {len(window.entries)} decision(s), {len(applied)} applied"]
    shown = window.entries if verbose else applied[:limit]
    for entry in shown:
        state = "applied" if entry.get("applied") else "not applied"
        files = ", ".join(entry.get("patched_files") or []) or "none"
        lines.append(
            f"- {state}: {entry.get('test', '?')} - strategy {entry.get('strategy', '?')}, "
            f"failure {entry.get('failure_type', '?')}, files {files}"
        )
    if window.skipped_lines:
        lines.append(f"- {window.skipped_lines} unreadable log line(s) ignored")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Healing decisions in a time window.")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--since", help="ISO time; entries at or after it")
    group.add_argument("--fork-point", metavar="TARGET", help="entries newer than the fork point")
    parser.add_argument("--all", action="store_true", help="list every decision, not just applied")
    args = parser.parse_args(argv)
    if args.since:
        window = read_window(args.log, parse_time(args.since))
    else:
        window = read_window(args.log, fork_point_time(args.fork_point), inclusive=False)
    print(describe(window, verbose=args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
