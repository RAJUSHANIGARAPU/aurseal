"""Merge the results of N shards into one run, and prove nothing went missing on the way.

A sharded run has a quiet failure mode of its own: a shard that crashed, was
cancelled, or selected nothing leaves fewer results behind, and fewer results
from a green suite still look green. So merging is two things:

* **combining** — JUnit XML files are summed into one ``<testsuites>`` document
  (stdlib ``xml.etree``); Allure result directories are copied into one, with
  ``environment.properties`` unioned and ``categories.json`` de-duplicated by
  name;
* **completeness** — every expected shard left a manifest, every shard selected
  at least one test, every test a shard selected actually reported, the shards
  agree on what was collected, and the union of executed node ids equals the full
  collection. Any gap is a failure of the merge, never a smaller green run.

The serial lane (``--shard=serial/N``, see :mod:`gate.pytest_shard`) is one more
shard to all of this: what it selected must report, and its exit status counts.
An *empty* serial lane has no say — selecting nothing is its normal answer when
no test needed it — but tests the parallel shards handed to it must not vanish
with it.

``python -m gate.merge --manifests reports/shards --shards 3 --junit ... --allure ...``
exits 1 when completeness fails. ``--summary`` adds one line per shard and a
verdict that also fails on a shard (with a say) that exited non-zero. The
run-trust verdict is then computed once over the shared ``TEST_RUN_ID`` by
``python -m gate.integrity``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gate.shard import SERIAL_INDEX, shard_label
from reporting.allure_environment import properties_text


@dataclass(frozen=True)
class JunitTotals:
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    time: float = 0.0


def _suites(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    return list(root.iter("testsuite"))


def merge_junit(paths: Iterable[str | Path], out: str | Path) -> JunitTotals:
    """Combine JUnit XML files into one ``<testsuites>``; counts are recomputed from cases."""
    merged = ET.Element("testsuites")
    tests = failures = errors = skipped = 0
    elapsed = 0.0
    for path in paths:
        root = ET.parse(path).getroot()
        for suite in _suites(root):
            merged.append(suite)
            for case in suite.iter("testcase"):
                tests += 1
                if case.find("failure") is not None:
                    failures += 1
                elif case.find("error") is not None:
                    errors += 1
                elif case.find("skipped") is not None:
                    skipped += 1
            elapsed += float(suite.get("time") or 0.0)
    totals = JunitTotals(tests, failures, errors, skipped, round(elapsed, 3))
    merged.set("tests", str(tests))
    merged.set("failures", str(failures))
    merged.set("errors", str(errors))
    merged.set("skipped", str(skipped))
    merged.set("time", str(totals.time))
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(out_path, encoding="utf-8", xml_declaration=True)
    return totals


def _unescape(text: str) -> str:
    out, i = [], 0
    mapping = {"n": "\n", "r": "\r", "t": "\t"}
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(mapping.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _parse_properties(text: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", "!")):
            continue
        i = 0
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] in "=:":
                break
            i += 1
        rows[_unescape(line[:i]).strip()] = _unescape(line[i + 1 :]).strip()
    return rows


_SPECIAL = {"environment.properties", "categories.json", "executor.json"}


def merge_allure(dirs: Iterable[str | Path], out: str | Path) -> int:
    """Copy every shard's Allure results into ``out``; returns the number of files copied.

    Result files are uniquely named, so copying is merging. The shared files are
    combined: environment rows unioned (first value wins), categories de-duplicated by
    name, executor taken from the first shard that has one.
    """
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    env_rows: dict[str, str] = {}
    categories: list[dict] = []
    seen_categories: set[str] = set()
    copied = 0
    for directory in dirs:
        source = Path(directory)
        if not source.is_dir():
            continue
        for path in sorted(source.iterdir()):
            if not path.is_file():
                continue
            if path.name == "environment.properties":
                for key, value in _parse_properties(path.read_text("utf-8")).items():
                    env_rows.setdefault(key, value)
            elif path.name == "categories.json":
                for category in json.loads(path.read_text("utf-8")):
                    name = str(category.get("name"))
                    if name not in seen_categories:
                        seen_categories.add(name)
                        categories.append(category)
            elif path.name == "executor.json":
                if not (target / path.name).exists():
                    shutil.copy2(path, target / path.name)
            else:
                shutil.copy2(path, target / path.name)
                copied += 1
    if env_rows:
        (target / "environment.properties").write_text(properties_text(env_rows), "utf-8")
    if categories:
        (target / "categories.json").write_text(json.dumps(categories, indent=2), "utf-8")
    return copied


def _empty_serial(index: int, selected: Mapping[int, set[str]] | None) -> bool:
    """The serial lane with nothing to run: it has no say in completeness or verdict."""
    return index == SERIAL_INDEX and not (selected or {}).get(SERIAL_INDEX)


def check_complete(
    expected: set[str],
    executed: Mapping[int, set[str]],
    shard_count: int | None = None,
    selected: Mapping[int, set[str]] | None = None,
    serial_expected: set[str] | None = None,
) -> list[str]:
    """Problems that make a merged run incomplete; ``[]`` when it is whole.

    ``serial_expected`` is what the parallel shards handed to the serial lane; when it
    is non-empty the serial lane must have left results like any other shard.
    """
    problems: list[str] = []
    if not expected:
        problems.append("expected collection is empty: nothing to prove completeness against")
    if shard_count is not None:
        missing = sorted(set(range(1, shard_count + 1)) - set(executed))
        problems += [f"shard {k}/{shard_count} left no results" for k in missing]
    if serial_expected and SERIAL_INDEX not in executed:
        problems.append(
            f"serial lane left no results ({len(serial_expected)} test(s) were handed to it)"
        )
    for index in sorted(executed):
        if not executed[index] and not _empty_serial(index, selected):
            problems.append(f"shard {shard_label(index)} executed zero tests")
    if selected:
        for index in sorted(selected):
            if not selected[index] and index != SERIAL_INDEX:
                problems.append(f"shard {shard_label(index)} selected zero tests")
            lost = sorted(selected[index] - executed.get(index, set()))
            if lost and index in executed:
                problems.append(
                    f"shard {shard_label(index)}: {len(lost)} selected test(s) never reported, "
                    f"e.g. {lost[0]}"
                )
    union = set().union(*executed.values()) if executed else set()
    not_run = sorted(expected - union)
    if not_run:
        problems.append(f"{len(not_run)} collected test(s) ran on no shard, e.g. {not_run[0]}")
    return problems


@dataclass(frozen=True)
class ShardManifests:
    count: int | None
    expected: set[str]
    selected: dict[int, set[str]]
    executed: dict[int, set[str]]
    exit_status: dict[int, int]
    problems: list[str]
    serial_expected: set[str] = field(default_factory=set)


def read_manifests(directory: str | Path) -> ShardManifests:
    """Read ``shard-K-of-N.{selected,executed}.json`` written by ``gate.pytest_shard``."""
    root = Path(directory)
    counts: set[int] = set()
    collections: dict[int, frozenset[str]] = {}
    selected: dict[int, set[str]] = {}
    executed: dict[int, set[str]] = {}
    exit_status: dict[int, int] = {}
    problems: list[str] = []
    serial_expected: set[str] = set()
    for path in sorted(root.glob("shard-*-of-*.selected.json")):
        data = json.loads(path.read_text("utf-8"))
        counts.add(int(data["count"]))
        collections[int(data["shard"])] = frozenset(data.get("collected", []))
        selected[int(data["shard"])] = set(data.get("selected", []))
        if int(data["shard"]) != SERIAL_INDEX:
            serial_expected |= set(data.get("serial_selected", []))
    for path in sorted(root.glob("shard-*-of-*.executed.json")):
        data = json.loads(path.read_text("utf-8"))
        counts.add(int(data["count"]))
        executed[int(data["shard"])] = set(data.get("executed", []))
        exit_status[int(data["shard"])] = int(data.get("exit_status", -1))
    if len(counts) > 1:
        problems.append(f"manifests disagree on the shard count: {sorted(counts)}")
    if len(set(collections.values())) > 1:
        problems.append("shards collected different test sets (code or plan differs per shard)")
    expected = set().union(*collections.values()) if collections else set()
    count = max(counts) if counts else None
    return ShardManifests(
        count, expected, selected, executed, exit_status, problems, serial_expected
    )


def shard_summary(manifests: ShardManifests) -> list[str]:
    """One line per shard: selected, executed, exit status (the serial lane last)."""
    indexes = sorted(set(manifests.selected) | set(manifests.executed))
    indexes.sort(key=lambda i: (i == SERIAL_INDEX, i))
    lines = []
    for index in indexes:
        count = manifests.count or "?"
        status = manifests.exit_status.get(index)
        line = (
            f"shard {shard_label(index)}/{count}: selected {len(manifests.selected.get(index, ()))}"
            f", executed {len(manifests.executed.get(index, ()))}"
            f", exit {'-' if status is None else status}"
        )
        if _empty_serial(index, manifests.selected):
            line += " (empty serial lane: no say)"
        lines.append(line)
    return lines


def verdict(
    manifests: ShardManifests, problems: Sequence[str], totals: JunitTotals | None = None
) -> str:
    """``PASS`` only when the run is whole, every shard with a say exited 0, and no test failed."""
    if problems:
        return "FAIL"
    for index, status in manifests.exit_status.items():
        if status != 0 and not _empty_serial(index, manifests.selected):
            return "FAIL"
    if totals is not None and (totals.failures or totals.errors):
        return "FAIL"
    return "PASS"


def merge_durations(directory: str | Path, out: str | Path) -> int:
    """Combine ``shard-*.durations.json`` into one ``{nodeid: seconds}`` file for next time."""
    merged: dict[str, float] = {}
    for path in sorted(Path(directory).glob("shard-*-of-*.durations.json")):
        merged.update(json.loads(path.read_text("utf-8")))
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, indent=2, sort_keys=True), "utf-8")
    return len(merged)


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.merge")
    parser.add_argument("--manifests", help="directory holding the shard manifests")
    parser.add_argument("--shards", type=int, help="number of shards that must be present")
    parser.add_argument("--junit", nargs="*", default=[], help="JUnit XML files to merge")
    parser.add_argument("--junit-out", default="reports/junit-merged.xml")
    parser.add_argument("--allure", nargs="*", default=[], help="Allure result directories")
    parser.add_argument("--allure-out", default="reports/allure-results")
    parser.add_argument("--durations-out", default="reports/durations.json")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print one line per shard and a verdict; exit 1 unless it is PASS",
    )
    args = parser.parse_args(argv)

    totals: JunitTotals | None = None
    if args.junit:
        totals = merge_junit(args.junit, args.junit_out)
        print(
            f"junit: {totals.tests} tests, {totals.failures} failures, {totals.errors} errors, "
            f"{totals.skipped} skipped -> {args.junit_out}",
            file=out,
        )
    if args.allure:
        print(f"allure: {merge_allure(args.allure, args.allure_out)} files", file=out)
    if not args.manifests:
        return 0

    manifests = read_manifests(args.manifests)
    merge_durations(args.manifests, args.durations_out)
    count = args.shards or manifests.count
    problems = manifests.problems + check_complete(
        manifests.expected,
        manifests.executed,
        count,
        manifests.selected,
        manifests.serial_expected,
    )
    if args.summary:
        for line in shard_summary(manifests):
            print(line, file=out)
    if problems:
        print("merge: INCOMPLETE", file=out)
        for problem in problems:
            print(f"  - {problem}", file=out)
    else:
        print(
            f"merge: complete — {len(manifests.expected)} tests across {count} shard(s)", file=out
        )
    if args.summary:
        result = verdict(manifests, problems, totals)
        print(f"verdict: {result}", file=out)
        return 0 if result == "PASS" else 1
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
