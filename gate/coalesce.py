"""Optional coalescing window for default-branch pushes. Off by default.

GitHub concurrency with ``cancel-in-progress: false`` already keeps at most one *pending*
run per group: a newer push replaces the pending one, so pushes that land while a gate runs
collapse into one follow-up run. This adds a deliberate window on top, for a repository
whose merges come in bursts: the first job of a default-branch push waits
``COALESCE_SECONDS`` (a repository variable; 0 or unset = off), then compares its own commit
with the default branch's current head. When they differ, a newer push exists and its own
run covers this commit, so this run writes "superseded by <sha>" to the step summary and the
gate jobs skip.

Never skipped: a dispatch, a schedule, a pull request, any branch but the default one. On any
error (an unreadable variable, ``git ls-remote`` failing) the gate runs: a window that cannot
decide must not cost a verdict.

``python -m gate.coalesce`` writes ``superseded=true|false`` to ``$GITHUB_OUTPUT``; exit 0.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

SECONDS_ENV = "COALESCE_SECONDS"
MAX_SECONDS = 180  # the job's own timeout-minutes keeps it on the critical-path budget


@dataclass(frozen=True)
class Decision:
    superseded: bool
    reason: str


def window_seconds(environ: Mapping[str, str]) -> int:
    """The configured window, clamped to ``0..MAX_SECONDS``; unreadable is 0 (off)."""
    raw = (environ.get(SECONDS_ENV) or "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return 0
    return max(0, min(value, MAX_SECONDS))


def applies(environ: Mapping[str, str]) -> bool:
    """Only a push to the default branch can be coalesced."""
    branch = environ.get("DEFAULT_BRANCH") or "main"
    return (
        environ.get("GITHUB_EVENT_NAME") == "push"
        and environ.get("GITHUB_REF") == f"refs/heads/{branch}"
    )


def remote_head(branch: str, run: Callable[..., subprocess.CompletedProcess]) -> str:
    done = run(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git ls-remote exited {done.returncode}: {done.stderr.strip()}")
    sha = done.stdout.split()[0] if done.stdout.split() else ""
    if len(sha) != 40:
        raise RuntimeError(f"git ls-remote returned no head for {branch}")
    return sha


def decide(
    environ: Mapping[str, str],
    *,
    sleep: Callable[[float], None] = time.sleep,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Decision:
    if not applies(environ):
        return Decision(False, "not a default-branch push: the window never applies")
    seconds = window_seconds(environ)
    if seconds == 0:
        return Decision(False, f"coalescing window off ({SECONDS_ENV} unset or 0)")
    sleep(seconds)
    own = environ.get("GITHUB_SHA", "")
    try:
        head = remote_head(environ.get("DEFAULT_BRANCH") or "main", run)
    except Exception as exc:  # noqa: BLE001 - any doubt runs the gate
        return Decision(False, f"could not read the default-branch head ({exc}); gate runs")
    if head == own:
        return Decision(False, f"still the newest commit after {seconds}s; gate runs")
    return Decision(True, f"superseded by {head}, covered by that run")


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    del argv  # no options: everything comes from the run's environment
    decision = decide(environ)
    print(f"coalesce: {decision.reason}")
    summary = environ.get("GITHUB_STEP_SUMMARY")
    if summary and decision.superseded:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"Gate skipped: {decision.reason}\n")
    output = environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"superseded={'true' if decision.superseded else 'false'}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
