"""``python -m checks.load --target URL --profile checks/load/profiles/smoke.json``.

Exit 0 when every threshold held, 1 when one was crossed, 2 when nothing could be
judged (no k6, no routes, a bad profile, an unreadable summary).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from checks._outcome import CheckOutcome, exit_code
from checks._sink import REPO_ROOT, JsonlSink
from checks.load.k6 import KIND
from checks.load.profile import LoadProfile, ProfileError
from checks.load.runner import K6Runner, run_load

DEFAULT_PROFILE = Path(__file__).resolve().parent / "profiles" / "smoke.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.load")
    parser.add_argument("--target", action="append", default=[], help="URL; repeatable")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--out", default=str(REPO_ROOT / "reports" / "load"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--k6", default=None, help="k6 binary (default: K6_BIN or PATH)")
    parser.add_argument("--no-record", action="store_true", help="do not write metric rows")
    args = parser.parse_args(argv)
    try:
        profile = LoadProfile.from_json(args.profile)
    except ProfileError as exc:
        outcome = CheckOutcome.unknown(KIND, f"profile refused: {exc}")
    else:
        outcome = run_load(
            args.target,
            profile,
            Path(args.out),
            runner=K6Runner(args.k6, args.timeout),
            sink=None if args.no_record else JsonlSink(),
        )
    print(json.dumps(outcome.summary(), indent=2, default=str))
    return exit_code(outcome)


if __name__ == "__main__":
    sys.exit(main())
