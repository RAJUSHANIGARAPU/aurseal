"""
Count what a pytest run's JUnit XML says, across every report file in a folder.

``failed`` is failures plus errors: an error in setup is as red as an assertion.
The gate word is ``pass`` only when something ran and nothing failed. A folder
with no XML at all is reported as *missing*, never as zero tests passed, and a
file that does not parse is listed by name rather than ending the count.

    python -m tools.slash.junit [--reports reports] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree

MESSAGE_CHARS = 300


@dataclass(frozen=True)
class Case:
    classname: str
    name: str
    kind: str  # "failure" or "error"
    message: str
    time: float

    @property
    def node(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name


@dataclass
class JUnitSummary:
    files: list[str] = field(default_factory=list)
    total: int = 0
    failed: int = 0
    skipped: int = 0
    cases: list[Case] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    durations: list[tuple[str, float]] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return self.total - self.failed - self.skipped

    @property
    def measured(self) -> bool:
        """True when at least one report file was read; zero files is not zero tests."""
        return bool(self.files)

    @property
    def gate(self) -> str:
        return "pass" if self.measured and self.total and not self.failed else "block"

    def slowest(self, count: int = 5) -> list[tuple[str, float]]:
        return sorted(self.durations, key=lambda item: item[1], reverse=True)[:count]

    def signature(self) -> str:
        """A stable fingerprint of what failed, used to spot an error that survives fixes."""
        return "\n".join(
            sorted(
                f"{c.node}: {c.message.splitlines()[0] if c.message else ''}" for c in self.cases
            )
        )


def find_reports(directory: str | Path) -> list[Path]:
    """The JUnit files directly in ``directory`` (sub-folders hold other report types)."""
    return sorted(Path(directory).glob("*.xml"))


def _seconds(raw: str | None) -> float:
    try:
        return float(raw or 0)
    except ValueError:
        return 0.0


def aggregate(paths: Iterable[str | Path]) -> JUnitSummary:
    summary = JUnitSummary()
    for path in paths:
        path = Path(path)
        try:
            root = ElementTree.parse(path).getroot()
        except (ElementTree.ParseError, OSError):
            summary.unreadable.append(str(path))
            continue
        summary.files.append(str(path))
        for case in root.iter("testcase"):
            summary.total += 1
            classname, name = case.get("classname", ""), case.get("name", "")
            elapsed = _seconds(case.get("time"))
            summary.durations.append((f"{classname}::{name}" if classname else name, elapsed))
            if case.find("skipped") is not None:
                summary.skipped += 1
                continue
            bad = case.find("failure")
            kind = "failure"
            if bad is None:
                bad, kind = case.find("error"), "error"
            if bad is None:
                continue
            summary.failed += 1
            text = bad.get("message") or (bad.text or "").strip()
            summary.cases.append(Case(classname, name, kind, text[:MESSAGE_CHARS], elapsed))
    return summary


def describe(summary: JUnitSummary) -> str:
    if not summary.measured:
        return (
            "Test results: missing - no JUnit XML in the reports folder. "
            "Run the suite with --junitxml (or the test-health-report command) first."
        )
    lines = [
        f"Test results: {summary.total} total, {summary.passed} passed, "
        f"{summary.failed} failed, {summary.skipped} skipped - gate: {summary.gate.upper()}"
    ]
    lines += [
        f"- {c.kind}: {c.node} - {c.message.splitlines()[0] if c.message else ''}"
        for c in summary.cases
    ]
    lines += [f"- unreadable report: {name}" for name in summary.unreadable]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--file", action="append", help="read these files instead of the folder")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = aggregate(args.file or find_reports(args.reports))
    if args.json:
        data = asdict(summary) | {
            "passed": summary.passed,
            "gate": summary.gate,
            "measured": summary.measured,
        }
        print(json.dumps(data, indent=2))
    else:
        print(describe(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
