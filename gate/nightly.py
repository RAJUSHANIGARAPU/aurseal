"""The nightly switch, and the one line every scheduled run writes about what it did not run.

A nightly that is turned off, or an opt-in job that cannot run on a schedule, produces no
red build — it produces no build line at all, which reads exactly like a quiet green. So a
scheduled run always writes one line to the step summary:

* the switch is off (repository variable ``NIGHTLY_ENABLED`` is ``false``): the line says
  so and names the variable, and every other job is skipped by its ``if:``;
* otherwise: every opt-in job that cannot run on a schedule, each with its reason.

``python -m gate.nightly summary`` prints the line, appends it to ``--summary`` (the
``$GITHUB_STEP_SUMMARY`` file) and writes ``enabled``, ``prefix`` and ``render_token`` to
``--github-output`` for the jobs that depend on them.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

from gate._env import env_bool

NIGHTLY_ENV = "NIGHTLY_ENABLED"
TOKEN_PRESENT_ENV = "RENDER_TOKEN_PRESENT"
NIGHTLY_PREFIX = "nightly"

# Jobs of tests.yml that never run on a schedule, and why. A test pins that each exists.
SCHEDULE_SKIPPED: Mapping[str, str] = {
    "visual-baselines": "manual only: baselines are taken by hand and reviewed before commit",
    "bringup": "runs only on bringup/<test> branches",
    "pr-feedback": "pull requests only",
}
TOKEN_LANE_JOB = "render"
TOKEN_LANE_REASON = "needs a bearer token and the RENDER_TOKEN secret is not set"


def nightly_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True unless the variable is explicitly false; unset means on."""
    return env_bool(NIGHTLY_ENV, True, env)


def skipped_on_schedule(token_present: bool) -> dict[str, str]:
    skipped = dict(SCHEDULE_SKIPPED)
    if not token_present:
        skipped[TOKEN_LANE_JOB] = TOKEN_LANE_REASON
    return skipped


def summary_line(enabled: bool, skipped: Mapping[str, str]) -> str:
    """The one line a scheduled run writes."""
    if not enabled:
        return (
            f"Nightly disabled: repository variable {NIGHTLY_ENV} is false, so this scheduled "
            "run ran nothing but the secret scan."
        )
    if not skipped:
        return "Nightly: every opt-in job could run on this schedule."
    parts = "; ".join(f"{job} ({reason})" for job, reason in sorted(skipped.items()))
    return f"Nightly: not run on a schedule - {parts}."


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(prog="python -m gate.nightly")
    sub = parser.add_subparsers(dest="command", required=True)
    summary = sub.add_parser("summary", help="write the scheduled-run line")
    summary.add_argument("--summary", default=env.get("GITHUB_STEP_SUMMARY"))
    summary.add_argument("--github-output", default=env.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)

    enabled = nightly_enabled(env)
    token_present = env_bool(TOKEN_PRESENT_ENV, False, env)
    line = summary_line(enabled, skipped_on_schedule(token_present))
    print(line)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"enabled={'true' if enabled else 'false'}\n")
            handle.write(f"prefix={NIGHTLY_PREFIX}\n")
            handle.write(f"render_token={'true' if enabled and token_present else 'false'}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
