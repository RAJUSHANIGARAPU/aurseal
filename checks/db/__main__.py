"""``python -m checks.db snapshot --url sqlite:///app.sqlite --queries q.json [--params '{...}']``.

Prints the snapshot as JSON (debugging aid). ``q.json`` is ``{name: sql}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from checks.db.probe import open_probe


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m checks.db")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--url", required=True)
    snap.add_argument("--queries", required=True)
    snap.add_argument("--params", default="{}")
    args = parser.parse_args(argv)
    with open(args.queries, encoding="utf-8") as handle:
        queries = json.load(handle)
    probe = open_probe(args.url)
    try:
        print(json.dumps(probe.snapshot(queries, json.loads(args.params)), indent=2), file=out)
    finally:
        probe.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
