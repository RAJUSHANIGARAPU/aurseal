"""Run trust: is this run evidence at all?

The tests assert the product is correct. This asserts the weaker claim
underneath that nothing else checks: *this run produced enough answers to be
read as a result*. A build that selected 40 tests, executed 8 and skipped 32
exits 0 and is coloured green; nothing in that output distinguishes "every
check held" from "almost nothing was checked". Saying so in a log line at WARN
changes no outcome, so this is a gate, not a report.

Four separate rules, each limited so that a lane is only judged on what it
actually tried to do. A rule with nothing to judge is *absent*, which is not a
pass, and the summary lists it so an empty check never looks like a healthy run.

1. Executed floor — fewer than ``min_executed`` tests produced an answer,
   judged only when the selection was at least that large (a deliberately
   narrow selection is not a shrunken run). Selecting nothing at all is
   already pytest's exit code 5.
2. Skip ratio — more than ``max_skip_ratio`` of the selection abstained
   (skipped, or never reported). Measured apart from the floor, because a run
   can execute a lot and still be mostly skips once the selection is large.
3. Detector verdicts — detectors (expected-red tests) were selected and fewer
   than ``min_verdicts`` tracked defects reached a verdict. From the build
   result alone, a detector that was skipped cannot be told apart from one
   that ran and saw the defect again.
4. Fixture provenance — tests that need acquired data were selected and fewer
   than ``min_acquired`` passing tests stood on data they actually acquired.

Each rule switches itself off at a threshold past where it can bite (0 for the
counts, >= 1.0 for the ratio), so disabling one is a value, not a separate flag
nobody remembers. Thresholds are fixed on purpose rather than learned from past
runs: a threshold that follows recent behaviour quietly accepts any regression
that survives a couple of builds.

Enforcement — ``fail`` (default), ``unstable`` or ``report`` — decides only
what a FAIL does to the run's exit status. Turning it down never turns the
finding off: the verdict is computed, printed and written in every mode.

Markers. ``run-trust.txt`` (first line machine-readable, then the boxed
summary) and ``run-trust.json`` (the numbers, plus every test's outcome).
Writing is best-effort and never raises, so failing to save the gate's verdict
cannot also wipe out the run's actual outcome.

Reruns. A second invocation of the same run — ``pytest --lf`` after a red, with
the same ``TEST_RUN_ID`` — merges into the marker its own run wrote: the
retried tests' new outcomes replace their old ones and the verdict is
recomputed over the whole run, instead of a one-test tally replacing a
thirty-test verdict. A marker from a different run (or lane) is ignored and
replaced.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from core.logger import logger
from gate._env import env_float, env_int, env_str
from gate.provenance import FixtureRow
from gate.verdicts import (
    COVERAGE,
    COVERAGE_NOT_APPLICABLE,
    EXPECTED_RED,
    NOT_APPLICABLE_PREFIX,
    REACHED,
    SILENT,
    VerdictRow,
)

# Configuration (environment variables).
ENFORCEMENT_ENV = "RUN_TRUST"
MIN_EXECUTED_ENV = "RUN_TRUST_MIN_EXECUTED"
MAX_SKIP_RATIO_ENV = "RUN_TRUST_MAX_SKIP_RATIO"
MIN_VERDICTS_ENV = "RUN_TRUST_MIN_VERDICTS"
MIN_ACQUIRED_ENV = "RUN_TRUST_MIN_ACQUIRED"
DIR_ENV = "RUN_TRUST_DIR"
LANE_ENV = "RUN_TRUST_LANE"

DEFAULT_DIR = Path("reports")
DEFAULT_LANE = "default"
TEXT_MARKER = "run-trust.txt"
JSON_MARKER = "run-trust.json"

VERDICT_LINE_FAIL = "RUN TRUST FAILED - THIS RUN IS NOT EVIDENCE"
VERDICT_LINE_PASS = "RUN TRUST OK"
VERDICT_LINE_EMPTY = "RUN TRUST UNJUDGED - nothing in this run was in scope"

_WIDTH = 100
_SILENCES_SHOWN = 12
_FIRST_LINE_RE = re.compile(r"^VERDICT=(\w+) ENFORCEMENT=(\w+)")


class Enforcement(str, Enum):
    FAIL = "fail"  # a FAIL verdict fails the run
    UNSTABLE = "unstable"  # a FAIL verdict is flagged, the exit status is unchanged
    REPORT = "report"  # recorded and printed, nothing else

    @classmethod
    def parse(cls, raw: str | None, default: Enforcement | None = None) -> Enforcement | None:
        if raw is None or not raw.strip():
            return default
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return default


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNJUDGED = "UNJUDGED"


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"  # setup or teardown broke: still an answer, a red one
    XFAILED = "xfailed"  # an expected-red test that reproduced: it ran
    SKIPPED = "skipped"
    NOT_RUN = "not_run"  # selected, never reported (interrupted, crashed worker)


EXECUTED = frozenset({Outcome.PASSED, Outcome.FAILED, Outcome.ERROR, Outcome.XFAILED})


@dataclass(frozen=True)
class Thresholds:
    min_executed: int = 10
    max_skip_ratio: float = 0.50
    min_verdicts: int = 1
    min_acquired: int = 1
    enforcement: Enforcement = Enforcement.FAIL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Thresholds:
        """Defaults overridden by ``RUN_TRUST_*``; junk values fall back to the default."""
        d = cls()
        return cls(
            min_executed=env_int(MIN_EXECUTED_ENV, d.min_executed, env),
            max_skip_ratio=env_float(MAX_SKIP_RATIO_ENV, d.max_skip_ratio, env),
            min_verdicts=env_int(MIN_VERDICTS_ENV, d.min_verdicts, env),
            min_acquired=env_int(MIN_ACQUIRED_ENV, d.min_acquired, env),
            enforcement=Enforcement.parse(env_str(ENFORCEMENT_ENV, None, env), d.enforcement)
            or d.enforcement,
        )


@dataclass(frozen=True)
class TestResult:
    """One selected test's final answer (reruns collapsed to the last attempt)."""

    __test__ = False  # not a pytest test class

    nodeid: str
    outcome: Outcome
    markers: tuple[str, ...] = ()
    reason: str = ""
    defect: str | None = None  # expected-red defect id, when the test carries one
    cleared: bool = False  # an expected-red test that unexpectedly passed
    failed_attempts: int = 0  # earlier attempts in this run that failed and were retried
    first_failure: str = ""  # the first failed attempt's message (a flaky pass keeps it)

    def to_json(self) -> dict:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        data["markers"] = list(self.markers)
        return data

    @classmethod
    def from_json(cls, nodeid: str, data: Mapping) -> TestResult | None:
        try:
            return cls(
                nodeid=nodeid,
                outcome=Outcome(data["outcome"]),
                markers=tuple(data.get("markers") or ()),
                reason=str(data.get("reason") or ""),
                defect=data.get("defect"),
                cleared=bool(data.get("cleared", False)),
                failed_attempts=int(data.get("failed_attempts") or 0),
                first_failure=str(data.get("first_failure") or ""),
            )
        except (KeyError, ValueError, TypeError):
            return None


@dataclass(frozen=True)
class Silence:
    """A test that produced no answer, and why — the actionable half of the line."""

    test: str
    outcome: str
    reason: str


@dataclass(frozen=True)
class RunTally:
    selected: int
    executed: int
    skipped: int
    detectors_selected: int = 0
    verdicts_reached: int = 0
    seeded_selected: int = 0
    passed_on_acquired: int = 0
    silences: tuple[Silence, ...] = ()
    detector_silences: tuple[Silence, ...] = ()
    # Skipped as not applicable on this environment: neither executed nor an abstention.
    out_of_scope: tuple[Silence, ...] = ()

    @property
    def in_scope(self) -> int:
        return self.selected - len(self.out_of_scope)

    @property
    def skip_ratio(self) -> float:
        return 0.0 if self.in_scope <= 0 else self.skipped / self.in_scope


@dataclass(frozen=True)
class Breach:
    rule: str
    observed: str
    required: str
    setting: str  # the variable that moves the bar
    why: str  # the sentence that stops the rule being deleted by the next person in a hurry


@dataclass(frozen=True)
class Result:
    verdict: Verdict
    enforcement: Enforcement
    breaches: tuple[Breach, ...] = ()
    not_applicable: tuple[str, ...] = ()

    @property
    def fails_the_build(self) -> bool:
        return self.verdict is Verdict.FAIL and self.enforcement is Enforcement.FAIL

    @property
    def unsettles_the_build(self) -> bool:
        return self.verdict is Verdict.FAIL and self.enforcement is Enforcement.UNSTABLE

    @property
    def headline(self) -> str:
        return {
            Verdict.FAIL: VERDICT_LINE_FAIL,
            Verdict.PASS: VERDICT_LINE_PASS,
            Verdict.UNJUDGED: VERDICT_LINE_EMPTY,
        }[self.verdict]


def build_tally(
    tests: Iterable[TestResult],
    verdict_rows: Sequence[VerdictRow] = (),
    fixture_rows: Sequence[FixtureRow] = (),
    detector_markers: Iterable[str] = ("known_defect",),
    seeded_markers: Iterable[str] = ("seeded",),
) -> RunTally:
    """Fold per-test outcomes, detector verdict rows and fixture rows into the numbers judged."""
    tests = list(tests)
    detector_markers = frozenset(detector_markers)
    seeded_markers = frozenset(seeded_markers)

    not_applicable_ids = {
        r.nodeid
        for r in verdict_rows
        if r.kind == COVERAGE and r.verdict == COVERAGE_NOT_APPLICABLE
    }

    def out_of_scope(t: TestResult) -> bool:
        return t.outcome is Outcome.SKIPPED and (
            t.reason.startswith(NOT_APPLICABLE_PREFIX) or t.nodeid in not_applicable_ids
        )

    scoped_out = tuple(
        Silence(t.nodeid, "not applicable", t.reason) for t in tests if out_of_scope(t)
    )
    silences = tuple(
        Silence(t.nodeid, t.outcome.value, t.reason)
        for t in tests
        if t.outcome not in EXECUTED and not out_of_scope(t)
    )
    executed = sum(1 for t in tests if t.outcome in EXECUTED)

    detector_tests = [t for t in tests if detector_markers.intersection(t.markers)]
    red_rows = [r for r in verdict_rows if r.kind == EXPECTED_RED]
    detectors = {t.nodeid for t in detector_tests} | {r.nodeid for r in red_rows}
    reached = {r.key for r in red_rows if r.verdict in REACHED}
    # Without a verdict row, an expected-red test still answers through its outcome:
    # it reproduced (xfailed) or it unexpectedly passed (cleared).
    reached |= {
        t.defect or t.nodeid for t in detector_tests if t.outcome is Outcome.XFAILED or t.cleared
    }
    detector_silences = tuple(
        Silence(r.nodeid, r.verdict, r.detail) for r in red_rows if r.verdict in SILENT
    ) + tuple(
        Silence(t.nodeid, t.outcome.value, t.reason)
        for t in detector_tests
        if t.outcome not in EXECUTED
    )

    seeded = [t for t in tests if seeded_markers.intersection(t.markers)]
    on_acquired = sum(
        1
        for t in tests
        if t.outcome is Outcome.PASSED and any(row.covers(t.nodeid) for row in fixture_rows)
    )
    return RunTally(
        selected=len(tests),
        executed=executed,
        skipped=len(silences),
        detectors_selected=len(detectors),
        verdicts_reached=len(reached),
        seeded_selected=len(seeded),
        passed_on_acquired=on_acquired,
        silences=silences,
        detector_silences=detector_silences,
        out_of_scope=scoped_out,
    )


def _percent(ratio: float) -> str:
    return f"{round(ratio * 100)}%"


class RunTrustGate:
    """Applies every rule that has something to say about a run."""

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.thresholds = thresholds or Thresholds()

    def evaluate(self, tally: RunTally) -> Result:
        t = self.thresholds
        breaches: list[Breach] = []
        absent: list[str] = []
        applied = 0

        # 1. Executed floor, judged only against a selection large enough to be held to it.
        if t.min_executed <= 0:
            absent.append(f"executed floor - disabled ({MIN_EXECUTED_ENV}=0)")
        elif tally.selected < t.min_executed:
            absent.append(
                f"executed floor - just {tally.selected} test(s) were selected, under "
                f"the floor of {t.min_executed}: a narrowed selection, not a shrunken run"
            )
        else:
            applied += 1
            if tally.executed < t.min_executed:
                breaches.append(
                    Breach(
                        "EXECUTED FLOOR",
                        f"{tally.executed} of {tally.selected} selected test(s) executed",
                        f"at least {t.min_executed}",
                        MIN_EXECUTED_ENV,
                        "a run that executes only part of the suite only speaks for that part, "
                        "and its exit code looks the same either way",
                    )
                )

        # 2. Skip ratio, independent of the floor on purpose.
        if t.max_skip_ratio >= 1.0:
            absent.append(f"skip ratio - disabled ({MAX_SKIP_RATIO_ENV} >= 1.0)")
        elif tally.selected <= 0:
            absent.append("skip ratio - nothing was selected (pytest exit code 5 covers this)")
        elif tally.in_scope <= 0:
            absent.append("skip ratio - every selected test is out of scope on this environment")
        else:
            applied += 1
            if tally.skip_ratio > t.max_skip_ratio:
                breaches.append(
                    Breach(
                        "SKIP RATIO",
                        f"{_percent(tally.skip_ratio)} abstained "
                        f"({tally.skipped} of {tally.in_scope} in scope)",
                        f"at most {_percent(t.max_skip_ratio)}",
                        MAX_SKIP_RATIO_ENV,
                        "skips exceed the allowed share of the selection - the skip reasons "
                        "below tell the story, the exit code does not",
                    )
                )

        # 3. Detector verdicts.
        if t.min_verdicts <= 0:
            absent.append(f"detector verdicts - disabled ({MIN_VERDICTS_ENV}=0)")
        elif tally.detectors_selected <= 0:
            absent.append("detector verdicts - no detector (expected-red test) was selected")
        else:
            applied += 1
            if tally.verdicts_reached < t.min_verdicts:
                breaches.append(
                    Breach(
                        "DETECTOR VERDICTS",
                        f"{tally.verdicts_reached} verdict(s) from "
                        f"{tally.detectors_selected} selected detector(s)",
                        f"at least {t.min_verdicts}",
                        MIN_VERDICTS_ENV,
                        "no tracked defect got a verdict, which is not the same as none being "
                        "open - a skipped detector is indistinguishable from one that "
                        "reproduced the defect",
                    )
                )

        # 4. Fixture provenance.
        if t.min_acquired <= 0:
            absent.append(f"fixture provenance - disabled ({MIN_ACQUIRED_ENV}=0)")
        elif tally.seeded_selected <= 0:
            absent.append("fixture provenance - no test needing acquired data was selected")
        else:
            applied += 1
            if tally.passed_on_acquired < t.min_acquired:
                breaches.append(
                    Breach(
                        "FIXTURE PROVENANCE",
                        f"{tally.passed_on_acquired} passing test(s) on acquired data, from "
                        f"{tally.seeded_selected} selected test(s) needing it",
                        f"at least {t.min_acquired}",
                        MIN_ACQUIRED_ENV,
                        "nothing green in this run stood on data it actually got - the lane "
                        "verified the harness, not the product",
                    )
                )

        if breaches:
            verdict = Verdict.FAIL
        elif applied == 0:
            verdict = Verdict.UNJUDGED
        else:
            verdict = Verdict.PASS
        return Result(verdict, t.enforcement, tuple(breaches), tuple(absent))

    # ------------------------------------------------------------------ summary

    def summary(self, tally: RunTally, result: Result) -> list[str]:
        """The boxed block a human reads; self-contained so nobody has to open an artifact."""
        box = ["+" + "=" * _WIDTH + "+", _row(result.headline), "+" + "=" * _WIDTH + "+"]
        box.append(
            _row(
                f"selected {tally.selected} . executed {tally.executed} . skipped "
                f"{tally.skipped} ({_percent(tally.skip_ratio)})"
                + (f" . not applicable {len(tally.out_of_scope)}" if tally.out_of_scope else "")
            )
        )
        box.append(
            _row(
                f"detectors selected {tally.detectors_selected} . verdicts reached "
                f"{tally.verdicts_reached}"
            )
        )
        box.append(
            _row(
                f"needing acquired data {tally.seeded_selected} . passing on acquired data "
                f"{tally.passed_on_acquired}"
            )
        )
        if result.breaches:
            box.append(_rule())
            for breach in result.breaches:
                box.append(_row(f"{breach.rule}: {breach.observed} - required {breach.required}"))
                box.extend(_wrap("    ", "why: " + breach.why))
                box.append(_row(f"    move the bar with {breach.setting}"))
        for title, silences in (
            ("what was skipped, and why:", tally.silences),
            ("detectors that reached no verdict:", tally.detector_silences),
            ("not applicable here (out of scope, not counted as skips):", tally.out_of_scope),
        ):
            if not silences:
                continue
            box.append(_rule())
            box.append(_row(title))
            for shown, silence in enumerate(silences):
                if shown == _SILENCES_SHOWN:
                    box.append(_row(f"    ... and {len(silences) - _SILENCES_SHOWN} more"))
                    break
                box.append(_row(f"    {silence.outcome.upper()}  {silence.test}"))
                # Wrapped, never truncated: the reason is the difference between an
                # outage and a deliberate exclusion.
                box.extend(_wrap("        ", silence.reason or "(no reason recorded)"))
        if result.not_applicable:
            box.append(_rule())
            box.append(_row("rules with nothing to judge (absent, not passed):"))
            for note in result.not_applicable:
                box.extend(_wrap("    ", note))
        box.append(_rule())
        box.extend(_wrap("", _closing_advice(result)))
        box.append("+" + "=" * _WIDTH + "+")
        return box


def _closing_advice(result: Result) -> str:
    if result.verdict is Verdict.PASS:
        return "All rules in scope passed: enough of the suite ran for this to count as a result."
    if result.verdict is Verdict.UNJUDGED:
        return "Nothing was in scope for any rule, so this run makes no claim either way."
    return {
        Enforcement.FAIL: "This run is not evidence and fails for that - repair whatever kept the "
        "tests from running instead of relaxing the threshold.",
        Enforcement.UNSTABLE: f"This run is not evidence. It is flagged unstable "
        f"({ENFORCEMENT_ENV}=unstable); the exit status is unchanged.",
        Enforcement.REPORT: f"This run is not evidence. Enforcement is disabled ({ENFORCEMENT_ENV}"
        f"=report), so the exit status is unchanged.",
    }[result.enforcement]


def _rule() -> str:
    return "+" + "-" * _WIDTH + "+"


def _row(text: str) -> str:
    raw = text or ""
    lead = min(len(raw) - len(raw.lstrip(" ")), _WIDTH // 2)
    body = " " + " " * lead + " ".join(raw.split())
    if len(body) > _WIDTH:
        body = body[: _WIDTH - 3] + "..."
    return "|" + body.ljust(_WIDTH) + "|"


def _wrap(indent: str, text: str) -> list[str]:
    flat = " ".join((text or "").split())
    if not flat:
        return []
    width = _WIDTH - 1 - len(indent)
    return [_row(indent + line) for line in textwrap.wrap(flat, width=width)]


# ---------------------------------------------------------------------- markers


def marker_paths(directory: str | Path, lane: str = DEFAULT_LANE) -> tuple[Path, Path]:
    suffix = "" if lane == DEFAULT_LANE else f"-{lane}"
    base = Path(directory)
    return (
        base / TEXT_MARKER.replace(".txt", f"{suffix}.txt"),
        base / JSON_MARKER.replace(".json", f"{suffix}.json"),
    )


def read_marker(directory: str | Path, lane: str = DEFAULT_LANE) -> dict | None:
    """The JSON marker in ``directory``, or None when absent or unreadable."""
    _, json_path = marker_paths(directory, lane)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def earlier_invocation_results(
    directory: str | Path, run_id: str, lane: str = DEFAULT_LANE
) -> dict[str, TestResult]:
    """Per-test outcomes an earlier invocation of THIS run recorded; empty for any other marker."""
    marker = read_marker(directory, lane)
    if not marker or marker.get("run_id") != run_id or marker.get("lane", DEFAULT_LANE) != lane:
        return {}
    earlier: dict[str, TestResult] = {}
    for nodeid, data in (marker.get("tests") or {}).items():
        if isinstance(data, Mapping):
            parsed = TestResult.from_json(nodeid, data)
            if parsed is not None:
                earlier[nodeid] = parsed
    return earlier


def write_markers(
    directory: str | Path,
    *,
    run_id: str,
    lane: str,
    tests: Iterable[TestResult],
    tally: RunTally,
    result: Result,
    gate: RunTrustGate,
) -> bool:
    """Write both markers. Returns False on any problem; never raises."""
    try:
        text_path, json_path = marker_paths(directory, lane)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        first = (
            f"VERDICT={result.verdict.value} ENFORCEMENT={result.enforcement.value.upper()} "
            f"RUN={run_id} LANE={lane}"
        )
        text_path.write_text(
            "\n".join([first, *gate.summary(tally, result)]) + "\n", encoding="utf-8"
        )
        thresholds = asdict(gate.thresholds)
        thresholds["enforcement"] = gate.thresholds.enforcement.value
        body = {
            "verdict": result.verdict.value,
            "enforcement": result.enforcement.value,
            "headline": result.headline,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "run_id": run_id,
            "lane": lane,
            "selected": tally.selected,
            "executed": tally.executed,
            "skipped": tally.skipped,
            "skip_ratio": tally.skip_ratio,
            "detectors_selected": tally.detectors_selected,
            "verdicts_reached": tally.verdicts_reached,
            "seeded_selected": tally.seeded_selected,
            "passed_on_acquired": tally.passed_on_acquired,
            "thresholds": thresholds,
            "breaches": [asdict(b) for b in result.breaches],
            "not_applicable": list(result.not_applicable),
            "out_of_scope": [asdict(s) for s in tally.out_of_scope],
            "tests": {t.nodeid: t.to_json() for t in tests},
        }
        json_path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.error("[run-gate] could not write the run-trust marker: %s", exc)
        return False


# -------------------------------------------------------------------------- CLI


def check_marker(
    directory: str | Path,
    lane: str = DEFAULT_LANE,
    *,
    require: bool = True,
    out=None,
) -> int:
    """What a CI step does with the marker: 0 ok, 1 fail-enforced FAIL, 2 marker missing.

    A missing marker is a failure by default. The marker is written on every
    run, so its absence means the gate never ran — which must not read the same
    as a pass.
    """
    out = out or sys.stdout
    text_path, _ = marker_paths(directory, lane)
    try:
        lines = text_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    match = _FIRST_LINE_RE.match(lines[0]) if lines else None
    if match is None:
        print(f"no run-trust marker at {text_path}: the gate did not run", file=out)
        return 2 if require else 0
    print("\n".join(lines[1:]), file=out)
    verdict, enforcement = match.group(1), match.group(2).lower()
    if verdict != Verdict.FAIL.value:
        return 0
    if enforcement == Enforcement.FAIL.value:
        return 1
    if enforcement == Enforcement.UNSTABLE.value and os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title=Run trust::{VERDICT_LINE_FAIL}", file=out)
    return 0


SHARD_VERDICTS = Path("verdicts") / "verdicts.jsonl"
SHARD_PROVENANCE = Path("data-origin.jsonl")


def merge_shard_markers(
    directories: Sequence[str | Path],
    *,
    run_id: str,
    lane: str,
    out_dir: str | Path,
    thresholds: Thresholds | None = None,
) -> tuple[Result | None, list[str]]:
    """Judge a sharded run once: the union of every shard's tests, at the full thresholds.

    Each shard is held to ``ceil(min / N)`` on its own; the merged run answers to the
    real floor. Every directory must hold a marker of the same run and lane (a shard
    without one never judged itself, which must not shrink the run quietly). A shard's
    ``verdicts/verdicts.jsonl`` and ``data-origin.jsonl``, when present, feed the detector
    and provenance rules. Returns the result (None when a marker is missing) and problems.
    """
    from gate.provenance import read_rows
    from gate.verdicts import read_verdict_rows

    problems: list[str] = []
    tests: dict[str, TestResult] = {}
    rows: list[VerdictRow] = []
    fixtures: list[FixtureRow] = []
    for directory in directories:
        marker = read_marker(directory, lane)
        if marker is None:
            problems.append(f"{directory}: no run-trust marker for lane {lane!r}")
            continue
        if marker.get("run_id") != run_id:
            problems.append(f"{directory}: marker is from run {marker.get('run_id')!r}")
            continue
        for nodeid, data in (marker.get("tests") or {}).items():
            parsed = TestResult.from_json(nodeid, data) if isinstance(data, Mapping) else None
            if parsed is not None:
                tests[nodeid] = parsed
        rows += read_verdict_rows(Path(directory) / SHARD_VERDICTS, run_id=run_id)
        fixtures += read_rows(Path(directory) / SHARD_PROVENANCE, run_id=run_id)
    if not directories:
        problems.append("no shard directory was given")
    if problems:
        return None, problems
    gate = RunTrustGate(thresholds or Thresholds.from_env())
    tally = build_tally(tests.values(), rows, fixtures)
    result = gate.evaluate(tally)
    write_markers(
        out_dir,
        run_id=run_id,
        lane=lane,
        tests=tests.values(),
        tally=tally,
        result=result,
        gate=gate,
    )
    return result, []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gate.integrity")
    parser.add_argument("--dir", default=os.environ.get(DIR_ENV) or str(DEFAULT_DIR))
    parser.add_argument("--lane", default=os.environ.get(LANE_ENV) or DEFAULT_LANE)
    parser.add_argument(
        "--allow-missing", action="store_true", help="exit 0 when no marker was written"
    )
    parser.add_argument(
        "--merge",
        nargs="+",
        metavar="DIR",
        help="judge a sharded run once: merge these shards' markers into --dir first",
    )
    parser.add_argument("--run-id", default=os.environ.get("TEST_RUN_ID", ""))
    args = parser.parse_args(argv)
    if args.merge is not None:
        _, problems = merge_shard_markers(
            args.merge, run_id=args.run_id, lane=args.lane, out_dir=args.dir
        )
        if problems:
            for problem in problems:
                print(f"run trust merge: {problem}")
            return 2
    return check_marker(args.dir, args.lane, require=not args.allow_missing)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
