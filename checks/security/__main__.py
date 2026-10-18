"""``python -m checks.security --tools bandit,pip-audit``.

Exit 0 when every requested tool ran and nothing blocks, 1 on a blocking finding or an
expired exemption, 2 when a requested tool gave no answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from checks._outcome import CheckOutcome, exit_code
from checks._sink import JsonlSink
from checks.security.allowlist import Allowlist
from checks.security.scan import DEFAULT_ALLOWLIST, DEFAULT_LOCKFILE, KIND, TOOLS, scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.security")
    parser.add_argument("--tools", default=",".join(TOOLS))
    parser.add_argument("--lockfile", default=str(DEFAULT_LOCKFILE))
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    parser.add_argument("--path", action="append", default=None, help="bandit target")
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args(argv)
    requested = [t.strip() for t in args.tools.split(",") if t.strip()]
    try:
        allowlist = Allowlist.load(args.allowlist)
    except ValueError as exc:
        outcome = CheckOutcome.error(KIND, f"allow-list rejected: {exc}")
    else:
        outcome = scan(
            requested,
            lockfile=Path(args.lockfile),
            allowlist=allowlist,
            bandit_paths=[Path(p) for p in args.path] if args.path else None,
            sink=None if args.no_record else JsonlSink(),
        )
    print(json.dumps(outcome.summary(), indent=2, default=str))
    return exit_code(outcome)


if __name__ == "__main__":
    sys.exit(main())
