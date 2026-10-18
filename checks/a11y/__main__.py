"""``python -m checks.a11y --route URL [--route URL] [--min-impact serious] [--known FILE]``.

Exit 0 when every route was scanned with no unexplained blocking violation, 1 on a
violation (or a known one that cleared), 2 when a route could not be scanned.
"""

from __future__ import annotations

import argparse
import json
import sys

from checks._browser import BrowserUnavailable
from checks._outcome import CheckOutcome, exit_code
from checks._sink import JsonlSink
from checks.a11y.assertions import load_known
from checks.a11y.axe import AxeUnavailable
from checks.a11y.judge import KIND, judge, record
from checks.a11y.policy import IMPACTS, A11yPolicy
from checks.a11y.runner import scan_routes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.a11y")
    parser.add_argument("--route", action="append", default=[], help="URL; repeatable")
    parser.add_argument("--min-impact", default="serious", choices=IMPACTS)
    parser.add_argument("--tag", action="append", default=None, help="axe tag; repeatable")
    parser.add_argument("--known", default=None, help="open-issue manifest file or dir")
    parser.add_argument("--browser", default="chromium")
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args(argv)
    policy = A11yPolicy(min_impact=args.min_impact, tags=tuple(args.tag or ("wcag2a", "wcag2aa")))
    manifest = load_known(args.known)
    if not args.route:
        outcome = CheckOutcome.unknown(KIND, "no routes were given")
    else:
        try:
            reports = scan_routes(args.route, policy, args.browser)
        except (AxeUnavailable, BrowserUnavailable) as exc:
            outcome = CheckOutcome.unknown(KIND, str(exc))
        else:
            if not args.no_record:
                record(reports, JsonlSink(), manifest)
            outcome = judge(reports, policy, manifest, record_verdicts=False)
    print(json.dumps(outcome.summary(), indent=2, default=str))
    return exit_code(outcome)


if __name__ == "__main__":
    sys.exit(main())
