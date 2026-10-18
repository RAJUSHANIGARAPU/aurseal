"""Binding an external configuration checkout in CI, and saying loudly when it did not bind.

Some offline tests check the suite against a configuration that lives in another
repository. CI checks that repository out (``actions/checkout`` into ``ext-config/``,
``fetch-depth: 1``), and this module turns the checkout into one of two outcomes:

* **bound** - a sentinel file exists and is non-empty. The checkout path, a pinned flag
  and the commit go to the tests through ``EXT_CONFIG_PATH``, ``EXT_CONFIG_PINNED`` and
  ``EXT_CONFIG_COMMIT``; the step summary shows repository, ref and the 8-character
  commit; the report environment panel records the full commit.
* **not bound** - missing directory, empty directory, missing or zero-byte sentinel. With
  ``EXT_CONFIG_REQUIRED`` true the step fails. Otherwise it emits a ``::warning::``, a
  boxed summary block listing the checks that did not run and the likely causes, and a
  run-trust marker for lane ``ext-config`` with verdict FAIL / enforcement UNSTABLE, so the
  run cannot look fully green.

The binding helper the tests call is :func:`bound_checkout` (``gate.requirement`` under
the hood): absent checkout skips, or fails when the binding is required.

``python -m gate.external_config verify --path ext-config --repo owner/name --ref main``
Exit 0 bound, or not bound and not required; 1 not bound and required.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gate._env import env_bool
from gate.requirement import require_checkout

PATH_ENV = "EXT_CONFIG_PATH"
PINNED_ENV = "EXT_CONFIG_PINNED"
COMMIT_ENV = "EXT_CONFIG_COMMIT"
REQUIRED_ENV = "EXT_CONFIG_REQUIRED"
LANE = "ext-config"
DEFAULT_CHECKOUT = "ext-config"
DEFAULT_SUB_PATH = "demo_sut/config"
DEFAULT_SENTINELS = ("subscription.machine.yaml",)
CHECKS_FILE = Path(__file__).resolve().parents[1] / "tests" / "external_config" / "checks.txt"
CAUSES = (
    "no access: a private repository needs the EXT_CONFIG_TOKEN secret",
    "wrong ref: the branch or tag does not exist in that repository",
    "sentinel missing: the expected file moved or the sub-path is wrong",
)


@dataclass(frozen=True)
class Binding:
    bound: bool
    reason: str
    path: Path


def check_sentinel(path: str | Path, sentinels: Sequence[str] = DEFAULT_SENTINELS) -> Binding:
    """Bound only when a sentinel file exists inside ``path`` and is non-empty."""
    base = Path(path)
    if not base.is_dir():
        return Binding(False, "missing", base)
    for name in sentinels:
        candidate = base / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return Binding(True, f"sentinel {name} present", base)
    for name in sentinels:
        if (base / name).is_file():
            return Binding(False, f"sentinel {name} is empty", base)
    return Binding(False, "no sentinel", base)


def read_checks(path: str | Path = CHECKS_FILE) -> list[str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def render_summary(
    binding: Binding,
    repo: str,
    ref: str,
    commit: str | None = None,
    checks: Sequence[str] = (),
    causes: Sequence[str] = CAUSES,
) -> str:
    """One line when bound; a warning block naming what was not verified otherwise."""
    if binding.bound:
        return f"External config bound: {repo}@{ref} commit {(commit or 'unknown')[:8]}"
    lines = [
        f"> **External config NOT bound** ({repo}@{ref}: {binding.reason}).",
        "> The binding tests did not run. Unverified checks:",
        *[f"> - {check}" for check in checks or ("(no checks declared)",)],
        "> Likely causes:",
        *[f"> - {cause}" for cause in causes],
    ]
    return "\n".join(lines)


def commit_of(path: str | Path) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    sha = done.stdout.strip()
    return sha if done.returncode == 0 and sha else None


def write_lane_marker(directory: str | Path, run_id: str, bound: bool, headline: str) -> None:
    """A run-trust marker for the binding lane, read by ``python -m gate.integrity``."""
    verdict, enforcement = ("PASS", "report") if bound else ("FAIL", "unstable")
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    first = f"VERDICT={verdict} ENFORCEMENT={enforcement.upper()} RUN={run_id} LANE={LANE}"
    (base / f"run-trust-{LANE}.txt").write_text(f"{first}\n{headline}\n", encoding="utf-8")
    body = {
        "verdict": verdict,
        "enforcement": enforcement,
        "headline": headline,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "run_id": run_id,
        "lane": LANE,
        "tests": {},
    }
    (base / f"run-trust-{LANE}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")


def bound_checkout(expected: tuple[str, ...] = DEFAULT_SENTINELS) -> tuple[Path, str | None]:
    """For a binding test: the checkout and its commit, or skip (fail when required)."""
    path = require_checkout(PATH_ENV, REQUIRED_ENV, expected=expected)
    commit = os.environ.get(COMMIT_ENV, "").strip() if env_bool(PINNED_ENV, False) else ""
    return path, commit or None


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="python -m gate.external_config")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="check the external checkout and report the binding")
    verify.add_argument("--path", default=DEFAULT_CHECKOUT, help="checkout directory")
    verify.add_argument("--sub-path", default=DEFAULT_SUB_PATH, help="config inside the checkout")
    verify.add_argument("--sentinel", action="append", default=None)
    verify.add_argument("--repo", default=environ.get("GITHUB_REPOSITORY", "this repository"))
    verify.add_argument("--ref", default="default branch")
    verify.add_argument("--checks-file", default=str(CHECKS_FILE))
    verify.add_argument("--marker-dir", default="reports")
    verify.add_argument("--github-env", default=environ.get("GITHUB_ENV"))
    verify.add_argument("--summary", default=environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args(argv)

    required = env_bool(REQUIRED_ENV, False, environ)
    config_dir = Path(args.path) / args.sub_path if args.sub_path else Path(args.path)
    binding = check_sentinel(config_dir, tuple(args.sentinel or DEFAULT_SENTINELS))
    run_id = environ.get("TEST_RUN_ID", "local")
    commit = commit_of(args.path) if binding.bound else None
    summary = render_summary(binding, args.repo, args.ref, commit, read_checks(args.checks_file))
    _append(args.summary, summary)
    write_lane_marker(args.marker_dir, run_id, binding.bound, summary.splitlines()[0])
    if binding.bound:
        print(summary)
        if args.github_env:
            exports = [
                f"{PATH_ENV}={config_dir.resolve()}",
                f"{PINNED_ENV}=true",
                f"{COMMIT_ENV}={commit or ''}",
            ]
            _append(args.github_env, "\n".join(exports))
        return 0
    message = (
        f"external config {args.repo}@{args.ref} is not bound ({binding.reason}); "
        "the binding tests did not run"
    )
    if required:
        print(f"{message}, and {REQUIRED_ENV} is true", file=sys.stderr)
        print(summary, file=sys.stderr)
        return 1
    print(f"::warning title=External config::{message}")
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
