"""``python -m checks.canary scan|selftest``.

Exit 0 when every target is CLEARED, 1 when a target is STILL_LEAKING (or the self-test
finds the scanner broken), 2 when the answer is incomplete (a target NOT_ASSESSED, an
unreadable input).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from checks._outcome import CheckOutcome, exit_code
from checks.canary import report as reports
from checks.canary.backends import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_TIMEOUT,
    CloudWatchBackend,
    FilesBackend,
    LogBackend,
    LokiBackend,
)
from checks.canary.canaries import load_artifacts
from checks.canary.scan import DEFAULT_GRACE, KIND, scan_targets, to_outcome
from checks.canary.selftest import run_selftest
from checks.canary.targets import load_targets


def _backend(args: argparse.Namespace) -> LogBackend:
    if args.backend == "files":
        return FilesBackend(args.log_root)
    if args.backend == "loki":
        if not args.loki_url:
            raise ValueError("--backend loki needs --loki-url")
        return LokiBackend(args.loki_url, token_env=args.loki_token_env, org_id=args.loki_org)
    return CloudWatchBackend(region=args.aws_region, profile=args.aws_profile)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m checks.canary")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="positive control: the scanner must report STILL_LEAKING")
    scan = sub.add_parser("scan", help="search logs for a run's canaries")
    scan.add_argument("--run-artifact", action="append", required=True, help="repeatable")
    scan.add_argument("--targets", required=True, help="target map (YAML or JSON)")
    scan.add_argument("--backend", choices=("files", "loki", "cloudwatch"), default="files")
    scan.add_argument("--log-root", default=".", help="files: base directory for sources")
    scan.add_argument("--loki-url", help="loki: http(s)://host:port")
    scan.add_argument("--loki-token-env", help="loki: variable holding a bearer token")
    scan.add_argument("--loki-org", help="loki: X-Scope-OrgID")
    scan.add_argument("--aws-region")
    scan.add_argument("--aws-profile")
    scan.add_argument("--limit", type=int, default=DEFAULT_ROW_LIMIT, help="row limit per query")
    scan.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per target")
    scan.add_argument(
        "--grace-seconds",
        type=float,
        default=DEFAULT_GRACE.total_seconds(),
        help="how long after the run ends logs may still arrive",
    )
    scan.add_argument("--format", choices=("json", "md"), default="json")
    scan.add_argument("--out-dir", help="also write canary-scan.json and canary-scan.md here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "selftest":
        outcome = run_selftest()
        print(json.dumps(outcome.summary(), indent=2))
        return exit_code(outcome)
    try:
        artifact = load_artifacts(args.run_artifact)
        targets = load_targets(args.targets)
        backend = _backend(args)
    except ValueError as exc:
        outcome = CheckOutcome.error(KIND, str(exc))
        print(json.dumps(outcome.summary(), indent=2))
        return exit_code(outcome)
    results = scan_targets(
        targets,
        artifact,
        backend,
        limit=args.limit,
        timeout=args.timeout,
        grace=timedelta(seconds=args.grace_seconds),
    )
    report = reports.to_json(results, artifact, backend.name)
    markdown = reports.to_markdown(report)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "canary-scan.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
        (out / "canary-scan.md").write_text(markdown, "utf-8")
    print(markdown if args.format == "md" else json.dumps(report, indent=2))
    return exit_code(to_outcome(results))


if __name__ == "__main__":
    sys.exit(main())
