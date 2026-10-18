"""Run one selected lane of tests and print a PASS / FAIL / SKIP line per test class.

A lane is a marker expression and/or a set of paths (for example every lifecycle
example). The lane runs as one pytest invocation with a JUnit XML report, and the
summary is read back from that report, not from pytest's console output, so the
table always matches what CI would publish::

    python scripts/run_lane.py -m lifecycle
    python scripts/run_lane.py tests/demo_examples/lifecycle --name lifecycle -- -n auto

Everything after ``--`` is passed to pytest unchanged. The exit code is pytest's own
(5 when the lane selected nothing), or 3 when pytest left no readable report.

Configuration: ``LANE_REPORT_DIR`` (default ``reports/lanes``) is where
``<name>.xml`` is written.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPORT_DIR_ENV = "LANE_REPORT_DIR"
DEFAULT_REPORT_DIR = Path("reports") / "lanes"
NO_REPORT = 3


@dataclass(frozen=True)
class ClassResult:
    """Outcome counts of the tests in one class (or module, for plain test functions)."""

    name: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def status(self) -> str:
        if self.failed:
            return "FAIL"
        if self.passed:
            return "PASS"
        return "SKIP"


def build_command(
    markexpr: str | None,
    paths: Sequence[str],
    junit_path: Path,
    extra: Sequence[str] = (),
) -> list[str]:
    """The pytest command line for a lane."""
    command = [sys.executable, "-m", "pytest", *paths]
    if markexpr:
        command += ["-m", markexpr]
    return [*command, f"--junitxml={junit_path}", *extra]


def summarise(junit_xml: str | Path) -> list[ClassResult]:
    """Per-class outcome counts from a JUnit XML report, in first-seen order.

    An ``error`` element counts as a failure; a test with both a failure and a
    teardown error is counted once.
    """
    root = ET.parse(junit_xml).getroot()
    counts: dict[str, list[int]] = {}
    for case in root.iter("testcase"):
        name = case.get("classname") or case.get("name") or "?"
        row = counts.setdefault(name, [0, 0, 0])
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            row[1] += 1
        elif "skipped" in tags:
            row[2] += 1
        else:
            row[0] += 1
    return [ClassResult(name, *row) for name, row in counts.items()]


def render(results: Sequence[ClassResult]) -> str:
    """One line per class, then a totals line."""
    if not results:
        return "no tests ran in this lane"
    width = max(len(r.name) for r in results)
    lines = [
        f"{r.status:<4}  {r.name:<{width}}  "
        f"{r.passed} passed, {r.failed} failed, {r.skipped} skipped"
        for r in results
    ]
    total = ClassResult(
        "total",
        sum(r.passed for r in results),
        sum(r.failed for r in results),
        sum(r.skipped for r in results),
    )
    lines.append(
        f"{total.status:<4}  {'total':<{width}}  "
        f"{total.passed} passed, {total.failed} failed, {total.skipped} skipped"
    )
    return "\n".join(lines)


def _parse(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    argv = list(argv)
    extra: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1 :]
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("paths", nargs="*", help="test paths to run (default: testpaths)")
    parser.add_argument("-m", "--markexpr", help="pytest marker expression selecting the lane")
    parser.add_argument("--name", help="lane name used for the report file")
    args = parser.parse_args(argv)
    if not args.markexpr and not args.paths:
        parser.error("give a marker expression (-m) or at least one path")
    return args, extra


def lane_name(args: argparse.Namespace) -> str:
    raw = args.name or args.markexpr or Path(args.paths[0]).name
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw).strip("_") or "lane"


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[[list[str]], int] = subprocess.call,
    environ: dict[str, str] | None = None,
) -> int:
    args, extra = _parse(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    report_dir = Path(env.get(REPORT_DIR_ENV) or DEFAULT_REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    junit = report_dir / f"{lane_name(args)}.xml"
    if junit.exists():
        junit.unlink()  # never summarise a previous run's report
    code = runner(build_command(args.markexpr, args.paths, junit, extra))
    try:
        results = summarise(junit)
    except (OSError, ET.ParseError) as exc:
        print(f"lane report {junit} is missing or unreadable: {exc}", file=sys.stderr)
        return code or NO_REPORT
    print(render(results))
    print(f"report: {junit}")
    return code


if __name__ == "__main__":
    sys.exit(main())
