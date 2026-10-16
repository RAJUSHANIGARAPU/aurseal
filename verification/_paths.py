"""Path addressing for nested dict/list results.

A concrete path is a tuple of segments: ``str`` for mapping keys, ``int`` for
list positions. It renders as ``items[0].amount``.

A pattern uses the same syntax plus two wildcards:

* ``*`` (or ``[*]``) matches exactly one segment, key or index;
* ``**`` matches zero or more segments, so ``**.id`` matches every ``id``.

``fnmatch`` is deliberately not used: ``[`` is a character class there, so
``items[*].id`` would silently mean something else.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Any

Segment = str | int
Path = tuple[Segment, ...]

_ANY = "*"
_ANY_DEPTH = "**"
_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+|\*)\]")
_MISSING = object()


def render(path: Sequence[Segment]) -> str:
    """Render a concrete path as ``a.b[0].c``; the root renders as ``$``."""
    out = ""
    for seg in path:
        if isinstance(seg, int):
            out += f"[{seg}]"
        else:
            out += f".{seg}" if out else seg
    return out or "$"


@lru_cache(maxsize=512)
def parse(expression: str) -> Path:
    """Parse ``a.b[0].c`` / ``items[*].id`` / ``**.id`` into segments."""
    if expression in ("", "$"):
        return ()
    segments: list[Segment] = []
    pos = 0
    for match in _TOKEN_RE.finditer(expression):
        gap = expression[pos : match.start()]
        if gap not in ("", "."):
            raise ValueError(f"Invalid path expression: {expression!r}")
        pos = match.end()
        key, index = match.groups()
        if key is not None:
            segments.append(key)
        elif index == _ANY:
            segments.append(_ANY)
        else:
            segments.append(int(index))
    if expression[pos:]:
        raise ValueError(f"Invalid path expression: {expression!r}")
    return tuple(segments)


def matches(pattern: str, path: Sequence[Segment]) -> bool:
    """True when the concrete ``path`` is addressed by ``pattern``."""
    return _match(parse(pattern), tuple(path))


def matches_any(patterns: Iterable[str], path: Sequence[Segment]) -> str | None:
    """Return the first pattern that addresses ``path``, or ``None``."""
    for pattern in patterns:
        if matches(pattern, path):
            return pattern
    return None


def _match(pattern: Path, path: Path) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == _ANY_DEPTH:
        return any(_match(rest, path[i:]) for i in range(len(path) + 1))
    if not path:
        return False
    if head == _ANY or head == path[0]:
        return _match(rest, path[1:])
    return False


def resolve(document: Any, expression: str, default: Any = _MISSING) -> Any:
    """Read the value at a concrete path; raise ``KeyError`` when absent."""
    current = document
    for seg in parse(expression):
        if seg in (_ANY, _ANY_DEPTH):
            raise ValueError(f"resolve() needs a concrete path, got {expression!r}")
        try:
            current = current[seg]
        except (KeyError, IndexError, TypeError):
            if default is _MISSING:
                raise KeyError(f"{expression!r} not found (missing at {seg!r})") from None
            return default
    return current
