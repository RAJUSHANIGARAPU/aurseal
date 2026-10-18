"""Visual baselines from the command line.

``python -m checks.visual compare BASELINE.png ACTUAL.png [--diff OUT.png]``
    exit 0 match, 1 mismatch / size change, 2 baseline missing or unreadable.
``python -m checks.visual approve --run RUN_ID [--name NAME ...] [--browser chromium]``
    copy the reviewed actuals from ``reports/visual/<RUN_ID>/`` over the baselines and
    record the approval. Commit the changed PNGs for review; CI never approves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from checks.visual.baselines import BaselineStore
from checks.visual.compare import MISSING_BASELINE, PillowUnavailable, compare
from checks.visual.snapshot import report_dir


def _compare(args: argparse.Namespace) -> int:
    baseline = Path(args.baseline)
    data = baseline.read_bytes() if baseline.is_file() else None
    try:
        diff = compare(
            data,
            Path(args.actual).read_bytes(),
            pixel_threshold=args.threshold,
            max_diff_ratio=args.max_ratio,
        )
    except PillowUnavailable as exc:
        print(exc)
        return 2
    print(f"{diff.outcome}: {diff.detail}")
    if args.diff and diff.diff_png:
        Path(args.diff).write_bytes(diff.diff_png)
        print(f"diff image: {args.diff}")
    if diff.outcome == MISSING_BASELINE:
        return 2
    return 0 if diff.matched else 1


def _approve(args: argparse.Namespace) -> int:
    source = report_dir(args.run)
    actuals = sorted(source.glob("*.actual.png"))
    if args.name:
        wanted = set(args.name)
        actuals = [p for p in actuals if p.name.removesuffix(".actual.png") in wanted]
        missing = wanted - {p.name.removesuffix(".actual.png") for p in actuals}
        if missing:
            print(f"no actual for {sorted(missing)} under {source}")
            return 2
    if not actuals:
        print(f"nothing to approve under {source}")
        return 2
    store = BaselineStore(browser=args.browser, platform=args.platform)
    for path in actuals:
        name = path.name.removesuffix(".actual.png")
        digest = store.approve(name, path, run_id=args.run)
        print(f"approved {name} -> {store.path(name)} sha256={digest[:12]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m checks.visual")
    sub = parser.add_subparsers(dest="command", required=True)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("baseline")
    cmp_.add_argument("actual")
    cmp_.add_argument("--diff", default=None)
    cmp_.add_argument("--threshold", type=float, default=0.1)
    cmp_.add_argument("--max-ratio", type=float, default=0.001)
    app = sub.add_parser("approve")
    app.add_argument("--run", required=True)
    app.add_argument("--name", action="append", default=[])
    app.add_argument("--browser", default="chromium")
    app.add_argument("--platform", default=None)
    args = parser.parse_args(argv)
    return _compare(args) if args.command == "compare" else _approve(args)


if __name__ == "__main__":
    sys.exit(main())
