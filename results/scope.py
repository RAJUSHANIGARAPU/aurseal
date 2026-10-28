"""Tests that are out of scope everywhere: removal candidates.

``gate.requirement.not_applicable`` (and ``runs_only_on`` / ``only_env``) records a
``not_applicable`` coverage verdict instead of abstaining. One environment saying "not
here" is normal. A test that said it in every one of its last N runs, on every lane,
never runs anywhere: it is dead weight that looks like coverage, so the status report
names it as a removal candidate (or a candidate for a lane that can run it).

``python -m results scope --runs 10`` prints the candidates; exit 0 either way (a report,
not a gate).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

DEFAULT_RUNS = 10


@dataclass(frozen=True)
class RemovalCandidate:
    nodeid: str
    runs: int  # how many recent runs were looked at (all not applicable)
    last_run: str


def removal_candidates(store: Any, runs: int = DEFAULT_RUNS) -> list[RemovalCandidate]:
    """Node ids whose final result in each of their last ``runs`` runs was not applicable.

    A run in which the test executed (or skipped for any other reason) breaks the streak;
    a test seen in fewer than ``runs`` runs is not judged yet.
    """
    if runs < 1:
        raise ValueError("runs must be >= 1")
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store.query("not_applicable_history"):
        history[row["nodeid"]].append(row)
    found = []
    for nodeid, rows in sorted(history.items()):
        recent = rows[:runs]
        if len(recent) < runs:
            continue
        if all(r["not_applicable"] and r["outcome"] == "skipped" for r in recent):
            found.append(RemovalCandidate(nodeid, len(recent), str(recent[0]["run_id"])))
    return found


def report(candidates: list[RemovalCandidate], runs: int) -> str:
    if not candidates:
        return f"no test was out of scope in each of its last {runs} runs"
    lines = [f"{len(candidates)} test(s) out of scope in each of their last {runs} runs:"]
    lines += [f"  removal candidate: {c.nodeid} (last run {c.last_run})" for c in candidates]
    return "\n".join(lines)
