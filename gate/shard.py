"""Split one test selection across N machines, deterministically and without coordination.

Every shard computes the full plan on its own and keeps its slice. That only works
if the plan is a pure function of its inputs, so:

* assignment is longest-processing-time greedy bin packing over per-test
  durations (seconds), with ties broken by a stable hash of the node id — never
  Python's per-process ``hash()``;
* a test with no recorded duration gets the median of the known ones (1.0 s when
  none are known), so new tests are still placed;
* tests that share backend state (``pinned``) are packed as one unit and land on
  the same shard: splitting them across machines is the parallel-E2E race in a
  new form;
* with the **serial lane** on, tests not shown to be isolated leave the parallel
  shards altogether and run in one pass after them (``--shard=serial/N``), so
  nothing else touches the environment while they do (:func:`is_serial`).

The plan is a partition — every node id appears exactly once. Durations come from
the results store when it is available, else from a JSON file
(``{"<nodeid>": seconds}``, written by the merge step), else equal weight.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import statistics
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DURATIONS = Path("reports/durations.json")
DURATIONS_ENV = "SHARD_DURATIONS"
_PINNED_UNIT = "\x00pinned"


SERIAL = "serial"
SERIAL_INDEX = 0  # the serial lane's key wherever shards are indexed by number


@dataclass(frozen=True)
class ShardSpec:
    index: int  # 1-based; SERIAL_INDEX for the serial lane
    count: int

    @property
    def serial(self) -> bool:
        return self.index == SERIAL_INDEX

    @property
    def label(self) -> str:
        """``2`` or ``serial``: the K of ``K/N``, as it appears in file names."""
        return SERIAL if self.serial else str(self.index)

    def __str__(self) -> str:
        return f"{self.label}/{self.count}"


def parse_shard(text: str) -> ShardSpec:
    """``"K/N"`` with 1 <= K <= N, or ``"serial/N"``. Anything else is a usage error."""
    try:
        left, right = text.split("/", 1)
        count = int(right)
        serial = left.strip() == SERIAL
        index = SERIAL_INDEX if serial else int(left)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"--shard expects K/N or serial/N (e.g. 2/3), got {text!r}") from exc
    if count < 1 or not (serial or 1 <= index <= count):
        raise ValueError(f"--shard {text!r}: need 1 <= K <= N")
    return ShardSpec(index, count)


def shard_label(index: int) -> str:
    """How a shard index reads in messages: ``2`` or ``serial``."""
    return SERIAL if index == SERIAL_INDEX else str(index)


def is_serial(
    markers: Iterable[str],
    serial_markers: Iterable[str],
    isolated_markers: Iterable[str] = (),
) -> bool:
    """Whether a test with ``markers`` belongs in the serial lane.

    It does when it carries one of ``serial_markers`` (shares state on purpose), or
    when ``isolated_markers`` is configured and it carries none of them (nobody has
    shown it is safe next to another process). An empty ``isolated_markers`` turns
    the second rule off.
    """
    names = set(markers)
    if names & set(serial_markers):
        return True
    isolated = set(isolated_markers)
    return bool(isolated) and not names & isolated


def _stable_key(nodeid: str) -> str:
    return hashlib.sha1(nodeid.encode("utf-8")).hexdigest()


def _weights(nodeids: Sequence[str], durations: Mapping[str, float]) -> dict[str, float]:
    known = [float(durations[n]) for n in nodeids if n in durations and durations[n] >= 0]
    fallback = statistics.median(known) if known else 1.0
    fallback = fallback if fallback > 0 else 1.0
    return {n: float(durations[n]) if n in durations else fallback for n in nodeids}


def plan_shards(
    nodeids: Sequence[str],
    durations: Mapping[str, float],
    n: int,
    pinned: Callable[[str], bool] = lambda _: False,
) -> list[list[str]]:
    """Partition ``nodeids`` into ``n`` lists, balanced by duration (LPT greedy)."""
    if n < 1:
        raise ValueError("shard count must be >= 1")
    unique = sorted(set(nodeids))
    weights = _weights(unique, durations)

    units: list[tuple[float, str, list[str]]] = []
    pinned_ids = [nid for nid in unique if pinned(nid)]
    pinned_set = set(pinned_ids)
    if pinned_ids:
        units.append((sum(weights[i] for i in pinned_ids), _stable_key(_PINNED_UNIT), pinned_ids))
    for nid in unique:
        if nid not in pinned_set:
            units.append((weights[nid], _stable_key(nid), [nid]))
    # Longest first; equal weights ordered by stable hash.
    units.sort(key=lambda u: (-u[0], u[1]))

    bins: list[list[str]] = [[] for _ in range(n)]
    heap = [(0.0, i) for i in range(n)]  # (load, shard index) - ties go to lowest index
    for weight, _key, members in units:
        load, index = heapq.heappop(heap)
        bins[index].extend(members)
        heapq.heappush(heap, (load + weight, index))
    return [sorted(b) for b in bins]


def shard_of(nodeid: str, plan: Sequence[Sequence[str]]) -> int:
    """1-based shard index holding ``nodeid``."""
    for index, members in enumerate(plan, start=1):
        if nodeid in members:
            return index
    raise KeyError(nodeid)


def plan_load(plan: Sequence[Sequence[str]], durations: Mapping[str, float]) -> list[float]:
    every = [nid for members in plan for nid in members]
    weights = _weights(every, durations)
    return [round(sum(weights[n] for n in members), 3) for members in plan]


def load_durations(path: str | Path | None = None) -> dict[str, float]:
    """``{nodeid: seconds}`` from a JSON file; ``{}`` when absent or unreadable."""
    target = Path(path) if path else DEFAULT_DURATIONS
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}


def durations_from_store(store) -> dict[str, float]:
    """Median duration per node id via the store's named query ``case_durations``.

    Expected rows: ``{"nodeid": str, "duration_ms": number}``.
    """
    rows = store.query("case_durations")
    return {
        str(r["nodeid"]): float(r["duration_ms"]) / 1000.0
        for r in rows
        if r.get("duration_ms") is not None
    }


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.shard")
    parser.add_argument("--plan", type=int, required=True, metavar="N")
    parser.add_argument("--durations", default=str(DEFAULT_DURATIONS))
    parser.add_argument("--ids", help="file with one node id per line (default: stdin)")
    args = parser.parse_args(argv)

    source = Path(args.ids).read_text("utf-8") if args.ids else sys.stdin.read()
    nodeids = [line.strip() for line in source.splitlines() if "::" in line]
    durations = load_durations(args.durations)
    plan = plan_shards(nodeids, durations, args.plan)
    for index, (members, load) in enumerate(
        zip(plan, plan_load(plan, durations), strict=True), start=1
    ):
        print(f"shard {index}/{args.plan}: {len(members)} tests, ~{load}s", file=out)
        for nid in members:
            print(f"  {nid}", file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
