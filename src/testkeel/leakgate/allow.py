"""The committed allowlist (``leakgate-allow.toml``) for personal-data false positives.

::

    [[allow]]
    rule = "pii.email"
    path = "tests/fixtures/**"
    reason = "invented addresses"
    line_contains = "optional public anchor"

Every entry needs a ``reason``. Denylist rules (``deny.*``) cannot be allowlisted here: an
entry would have to point at the hit and so describe it. A denylist false positive is
fixed by editing the private list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

from testkeel.leakgate.pii import RULES

FILE_NAME = "leakgate-allow.toml"


class AllowError(ValueError):
    pass


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """``**`` spans directories, ``*`` and ``?`` stay inside one path segment."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


@dataclass(frozen=True)
class Allow:
    rule: str
    path: re.Pattern[str]
    reason: str
    line_contains: str | None = None

    def covers(self, rule: str, path: str, line: str) -> bool:
        if rule != self.rule or not self.path.match(path):
            return False
        return self.line_contains is None or self.line_contains in line


def parse(text: str, source: str = FILE_NAME) -> tuple[Allow, ...]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise AllowError(f"{source}: not valid TOML: {exc}") from None
    entries = data.get("allow", [])
    if not isinstance(entries, list):
        raise AllowError(f"{source}: 'allow' must be an array of tables")
    out = []
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise AllowError(f"{source}: allow entry {number} is not a table")
        rule, path, reason = entry.get("rule"), entry.get("path"), entry.get("reason")
        if isinstance(rule, str) and rule.startswith("deny."):
            raise AllowError(
                f"{source}: allow entry {number}: denylist rules cannot be allowlisted; "
                "edit the private list instead"
            )
        if rule not in RULES:
            raise AllowError(f"{source}: allow entry {number}: unknown rule {rule!r}")
        if not isinstance(path, str) or not path:
            raise AllowError(f"{source}: allow entry {number}: 'path' is required")
        if not isinstance(reason, str) or not reason.strip():
            raise AllowError(f"{source}: allow entry {number}: 'reason' is required")
        anchor = entry.get("line_contains")
        if anchor is not None and not isinstance(anchor, str):
            raise AllowError(f"{source}: allow entry {number}: 'line_contains' must be text")
        out.append(Allow(rule, glob_to_regex(path), reason, anchor))
    return tuple(out)


def load(path: Path | None) -> tuple[Allow, ...]:
    if path is None or not path.is_file():
        return ()
    return parse(path.read_text(encoding="utf-8"), path.name)
