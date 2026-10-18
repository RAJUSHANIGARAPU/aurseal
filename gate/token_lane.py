"""An opt-in lane that runs only with a supplied bearer token.

The document-render checks (marker ``render_check``) authenticate only with a bearer
token, which CI cannot obtain non-interactively. So they run in their own job, only when
asked for and given a token, with their results kept apart from the gate's, and the job
refuses to start (rather than skip everything and go green) when asked without a token.

* :func:`should_run` - dispatch with the run-token-lane input, or a scheduled run on the
  default branch with the ``RENDER_TOKEN`` secret set. Otherwise skipped, with a reason.
* :func:`guard` - the job's first step: auth mode must be ``token`` and the token
  non-empty, or nothing starts.
* :func:`build_command` - the lane marker only, no reruns, results into
  ``reports/allure-results-render/`` and ``reports/junit-render.xml``. The workflow's
  literal pytest step is pinned to it. The token goes through the environment only
  (:func:`child_env`), never argv.

``python -m gate.token_lane guard`` / ``run [--dry-run]``. Exit 0 ok, 2 refused.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence

LANE = "render"
MARKER = "render_check"
TEST_DIR = "tests/render_lane"
RESULTS_DIR = "reports/allure-results-render"
JUNIT_PATH = "reports/junit-render.xml"
REQUIRED_ENV = "RENDER_REQUIRED"
TOKEN_ENV = "SUPPLIED_TOKEN"
AUTH_MODE_ENV = "AUTH_MODE"
SCHEDULE_SECRET = "RENDER_TOKEN"


def should_run(
    event: str,
    ref: str,
    *,
    flag: bool = False,
    token_present: bool = False,
    default_branch: str = "main",
) -> tuple[bool, str]:
    if event == "workflow_dispatch":
        if flag:
            return True, "asked for on dispatch"
        return False, "not asked for (run-token-lane input is false)"
    if event == "schedule":
        if ref not in (default_branch, f"refs/heads/{default_branch}"):
            return False, f"scheduled runs use the {default_branch} branch only"
        if token_present:
            return True, "scheduled, token available"
        return False, f"scheduled, but the {SCHEDULE_SECRET} secret is not set"
    return False, f"{event} runs never run the token lane"


def guard(auth_mode: str | None, token: str | None) -> str | None:
    """None when the lane may start; otherwise the reason it must not."""
    if (auth_mode or "").strip().lower() != "token":
        return (
            f"the {LANE} lane needs auth mode 'token' (got {auth_mode or 'unset'!r}). "
            "Start the run with auth_mode=token and a token, or leave the lane off."
        )
    if not (token or "").strip():
        return (
            f"the {LANE} lane needs a bearer token in {TOKEN_ENV}: pass the token input on "
            f"dispatch or set the {SCHEDULE_SECRET} secret, or leave the lane off."
        )
    return None


def build_command() -> list[str]:
    return [
        "pytest",
        TEST_DIR,
        "-m",
        MARKER,
        "-n",
        "0",
        f"--alluredir={RESULTS_DIR}",
        f"--junitxml={JUNIT_PATH}",
    ]


def child_env(token: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(
        {
            TOKEN_ENV: token,
            AUTH_MODE_ENV: "token",
            REQUIRED_ENV: "true",
            "RUN_TRUST": env.get("RUN_TRUST") or "fail",
            "RUN_TRUST_LANE": LANE,
        }
    )
    return env


def _spawn(argv: list[str], env: dict[str, str]) -> int:
    return subprocess.run([sys.executable, "-m", *argv], env=env, check=False).returncode


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    spawn: Callable[[list[str], dict[str, str]], int] = _spawn,
) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="python -m gate.token_lane")
    parser.add_argument("command", choices=("guard", "run"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    token = environ.get(TOKEN_ENV, "")
    refusal = guard(environ.get(AUTH_MODE_ENV), token)
    if refusal:
        print(f"token lane: {refusal}", file=sys.stderr)
        return 2
    print("token lane: auth mode token, token present")
    if args.command == "guard":
        return 0
    command = build_command()
    print("token lane:", shlex.join(command))
    if args.dry_run:
        return 0
    return spawn(command, child_env(token, environ))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
