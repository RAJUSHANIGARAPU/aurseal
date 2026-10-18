"""``python -m testkeel.leakgate scan [--tree DIR] [--message-file [LABEL=]FILE] ...``"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from testkeel.leakgate.gate import run
from testkeel.leakgate.report import emit


def _message(value: str) -> tuple[str, Path]:
    label, sep, path = value.partition("=")
    if sep and label and "/" not in label:
        return label, Path(path)
    return Path(value).stem or "message", Path(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m testkeel.leakgate")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan a tree, message files or a commit range")
    scan.add_argument("--tree", action="append", type=Path, default=[], help="directory")
    scan.add_argument(
        "--message-file",
        action="append",
        type=_message,
        default=[],
        metavar="[LABEL=]FILE",
        help="text such as a PR title or body; findings read LABEL:line",
    )
    scan.add_argument("--range", dest="commit_range", help="commit messages and added lines")
    scan.add_argument(
        "--history", action="store_true", help="every commit message and added line, all refs"
    )
    scan.add_argument(
        "--repo", type=Path, default=Path("."), help="repository for --range/--history"
    )
    scan.add_argument("--denylist", type=Path, help="list file (default: env, then config)")
    scan.add_argument("--allow", type=Path, help="allowlist (default: <tree>/leakgate-allow.toml)")
    scan.add_argument("--missing-list", choices=["fail", "warn"], default="fail")
    scan.add_argument(
        "--fork",
        action="store_true",
        help="no denylist by design: exit 3 (not assessed) unless a built-in rule hits",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trees = list(args.tree)
    if not trees and not args.message_file and not args.commit_range and not args.history:
        trees = [Path(".")]
    outcome = run(
        trees=trees,
        messages=args.message_file,
        commit_range=args.commit_range,
        history=args.history,
        range_root=args.repo,
        denylist_path=args.denylist,
        allow_path=args.allow,
        missing_list=args.missing_list,
        fork=args.fork,
    )
    emit(outcome, sys.stdout, sys.stderr)
    return outcome.code


if __name__ == "__main__":
    sys.exit(main())
