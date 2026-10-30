"""Matching canaries in a log line, with token boundaries.

A canary counts only when it is not part of a longer token: the characters on either side
must not be a letter, digit, ``_`` or ``-``. ``canary-run-1`` is therefore not found inside
``canary-run-10`` or ``canary-run-1-x``. Matching is case-insensitive, and an IBAN may be
printed in groups separated by single spaces.

Only counts leave this module; the line itself is never returned.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from checks.canary.canaries import KIND_ACCOUNT, KINDS, Canary

_BOUNDARY_BEFORE = r"(?<![\w-])"
_BOUNDARY_AFTER = r"(?![\w-])"


def _escape(canary: Canary) -> str:
    if canary.kind == KIND_ACCOUNT:
        compact = canary.value.replace(" ", "")
        return " ?".join(re.escape(ch) for ch in compact)
    return re.escape(canary.value)


def prefilter_pattern(canaries: Sequence[Canary]) -> str:
    """A case-insensitive alternation (RE2-safe, no look-around) to narrow a log search.

    It over-matches by design (no boundaries): the backends use it to fetch candidate lines
    and :class:`Matcher` decides.
    """
    parts = sorted({_escape(c) for c in canaries}, key=lambda p: (-len(p), p))
    return "(?i)(?:" + "|".join(parts) + ")" if parts else ""


class Matcher:
    """Classify the canary kinds present in a line."""

    def __init__(self, canaries: Sequence[Canary]) -> None:
        self._by_kind: dict[str, re.Pattern[str]] = {}
        for kind in KINDS:
            parts = sorted({_escape(c) for c in canaries if c.kind == kind})
            if parts:
                self._by_kind[kind] = re.compile(
                    _BOUNDARY_BEFORE + "(?:" + "|".join(parts) + ")" + _BOUNDARY_AFTER,
                    re.IGNORECASE,
                )

    def kinds_in(self, line: str) -> list[str]:
        return [kind for kind, pattern in self._by_kind.items() if pattern.search(line)]
