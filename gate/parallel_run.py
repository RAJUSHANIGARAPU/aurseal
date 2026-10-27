"""The small decisions behind ``scripts/run-parallel.sh``, kept in Python so they are tested.

The local parallel runner starts N shard processes and the serial lane, then merges
(:mod:`gate.merge`). What it selects, what each process must execute and who served
which shard are answered here:

* ``selection`` — the ``-m`` expression of the gate command CI's browser job runs
  (:func:`gate.run_inputs.default_command`), so a local parallel run and CI agree on
  what "the suite" is;
* ``floor --workers N [--serial]`` — the run-trust executed floor
  (``RUN_TRUST_MIN_EXECUTED``, :mod:`gate.integrity`) for one process: the whole
  run's floor split over the shards, rounded up, and 0 for the serial lane, which may
  legitimately run nothing;
* ``leases FILE...`` — which pool user each shard leased (the files
  ``USER_POOL_LEASE_RECORD`` points at), and how many shards each user served.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

NO_USER = "no pool user (exported or configured login, or no login at all)"


def selection() -> str:
    """The marker expression CI's gate command selects."""
    from gate.run_inputs import default_command

    argv = default_command()
    return argv[argv.index("-m") + 1]


def shard_floor(total: int, workers: int, serial: bool = False) -> int:
    """One process's share of the executed floor: ``ceil(total / workers)``; 0 when serial."""
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if serial or total <= 0:
        return 0
    return math.ceil(total / workers)


def total_floor(environ: Mapping[str, str] | None = None) -> int:
    from gate.integrity import Thresholds

    return Thresholds.from_env(environ).min_executed


def read_leases(files: Sequence[str | Path]) -> dict[str, dict | None]:
    """``{shard name: lease record or None}``; a missing or unreadable record is None."""
    leases: dict[str, dict | None] = {}
    for name in files:
        path = Path(name)
        shard = path.name.removesuffix(".lease.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            leases[shard] = data if {"index", "username"} <= set(data) else None
        except (OSError, ValueError):
            leases[shard] = None
    return leases


def lease_lines(leases: Mapping[str, dict | None]) -> list[str]:
    lines = []
    for shard, data in leases.items():
        user = f"pool user {data['index']} ({data['username']})" if data else NO_USER
        lines.append(f"{shard}: {user}")
    served = Counter(data["username"] for data in leases.values() if data)
    for username, count in sorted(served.items()):
        lines.append(f"{username} served {count} shard(s)")
    return lines


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.parallel_run")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selection", help="the -m expression of CI's gate command")
    floor = sub.add_parser("floor", help="one process's executed floor")
    floor.add_argument("--workers", type=int, required=True)
    floor.add_argument("--serial", action="store_true")
    leases = sub.add_parser("leases", help="which pool user each shard leased")
    leases.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    if args.command == "selection":
        print(selection(), file=out)
    elif args.command == "floor":
        try:
            print(shard_floor(total_floor(), args.workers, args.serial), file=out)
        except ValueError as exc:
            print(f"floor: {exc}", file=sys.stderr)
            return 2
    else:
        for line in lease_lines(read_leases(args.files)):
            print(line, file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
