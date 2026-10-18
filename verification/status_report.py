"""End-of-run status: what the detectors, coverage tests and a fixture lane established.

Generated from the run's own verdict records, never maintained by hand: a
status table someone has to remember to update describes a suite that no
longer exists. Three sections, each leading with the number that hides
behind a green build:

* **Tracked defects**, one row per defect id, aggregated by severity rather
  than arrival order (``signature_mismatch > closed_regressed > reproduces >
  held_empty > cleared > closed_pass``). A defect that spans several tests
  or reruns reports ``cleared`` only when *every* record cleared, so one late
  pass cannot announce a fix that has not landed everywhere.
* **Coverage verdicts**, best outcome per subject. ``proved`` is the only row
  that is release evidence; a run with none says ``NO COVERAGE VERDICT
  GIVEN`` instead of letting silence read as success.
* **Lane health** for tests carrying a lane marker (``VERDICT_LANE_MARKERS``,
  default ``seeded``): tests that must provision their own data before they
  can assert anything. When no lane test passed, one of four headlines says
  which failure it was, because each has a different remedy and all of them
  exit 0 or look alike at the build level.

A lane test reports that it really provisioned its data with
``mark_provisioned(request.node)``; that receipt, not the outcome, is what the
"did the lane provision anything?" question is answered from.

The report is written by the xdist controller (or the only process), to
``$VERDICT_STATUS_FILE`` (default ``reports/verdicts/verdict-status.md``), and
only when there is something to report. It never fails a run: enforcement
belongs to a gate that reads the same records.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from verification.verdicts import (
    RUN_ID_ENV,
    CoverageOutcome,
    Kind,
    RedVerdict,
    file_size,
    flatten,
    read_verdicts,
)

logger = logging.getLogger(__name__)

STATUS_FILE_ENV = "VERDICT_STATUS_FILE"
DEFAULT_STATUS_FILE = Path("reports") / "verdicts" / "verdict-status.md"
LANE_MARKERS_ENV = "VERDICT_LANE_MARKERS"
DEFAULT_LANE_MARKERS = ("seeded",)
PROVISIONED_PROPERTY = "provisioned"
FAILURE_KIND_PROPERTY = "failure_kind"

NO_DETECTOR_VERDICT = "NO DETECTOR GAVE A VERDICT"
NO_PROOF = "NO COVERAGE VERDICT GIVEN"
LANE_PROVISIONED_NOTHING = "LANE PROVISIONED NOTHING"
LANE_ERRORED_BEFORE_ASSERTING = "LANE ERRORED BEFORE ASSERTING"
LANE_NOTHING_HELD = "EVERY LANE ASSERTION FAILED"
LANE_PROVISIONED_BUT_ASSERTED_NOTHING = "LANE PROVISIONED BUT ASSERTED NOTHING"

_DEFECT_RANK = {
    RedVerdict.SIGNATURE_MISMATCH.value: 6,
    RedVerdict.CLOSED_REGRESSED.value: 5,
    RedVerdict.REPRODUCES.value: 4,
    RedVerdict.HELD_EMPTY.value: 3,
    RedVerdict.CLEARED.value: 2,
    RedVerdict.CLOSED_PASS.value: 1,
}
_COVERAGE_RANK = {
    CoverageOutcome.PROVED.value: 4,
    CoverageOutcome.RECORDED.value: 3,
    CoverageOutcome.HELD_EMPTY.value: 2,
    CoverageOutcome.ABSTAINED.value: 1,
}
_LANE_RANK = {"passed": 5, "failed": 4, "errored": 3, "aborted": 2, "skipped": 1}

_DEFECT_MEANING = {
    "signature_mismatch": "failed, but NOT because of the tracked defect",
    "closed_regressed": "closed, but reproduces again: reopen it",
    "reproduces": "still broken, as tracked",
    "held_empty": "**could not tell**: control or precondition failed",
    "cleared": "no longer reproduces: verify, close, unwrap the detector",
    "closed_pass": "closed and the fix holds",
}
_COVERAGE_MEANING = {
    "proved": "exercised and asserted: **the only row that is release evidence**",
    "recorded": "observed and written down, not asserted",
    "held_empty": "passed only because the data was missing",
    "abstained": "**no verdict**: a precondition could not be arranged",
}


@dataclass(frozen=True)
class Aggregate:
    """The worst (or best, for coverage) verdict for one key, with the detail behind it."""

    verdict: str
    detail: str
    records: int


def mark_provisioned(node: pytest.Item, how: str = "fresh") -> None:
    """Receipt that a lane test really provisioned its data (call after provisioning succeeds)."""
    node.user_properties.append((PROVISIONED_PROPERTY, how))


def aggregate_defects(records: Iterable[Mapping[str, Any]]) -> dict[str, Aggregate]:
    """Per defect id, the most severe expected-red verdict across every record."""
    return _aggregate(records, Kind.EXPECTED_RED.value, "ticket", _DEFECT_RANK)


def aggregate_coverage(records: Iterable[Mapping[str, Any]]) -> dict[str, Aggregate]:
    """Per coverage subject, the outcome closest to evidence across every record."""
    return _aggregate(records, Kind.COVERAGE.value, "subject", _COVERAGE_RANK)


def _aggregate(
    records: Iterable[Mapping[str, Any]], kind: str, key: str, rank: Mapping[str, int]
) -> dict[str, Aggregate]:
    out: dict[str, Aggregate] = {}
    for record in records:
        if record.get("kind") != kind:
            continue
        name = record.get(key) or record.get("ticket") or record.get("nodeid") or "?"
        verdict = str(record.get("verdict", ""))
        prior = out.get(name)
        count = 1 if prior is None else prior.records + 1
        if prior is None or rank.get(verdict, 0) > rank.get(prior.verdict, 0):
            out[name] = Aggregate(verdict, str(record.get("detail", "")), count)
        else:
            out[name] = Aggregate(prior.verdict, prior.detail, count)
    return dict(sorted(out.items()))


def lane_headline(total: int, failed: int, errored: int, provisioned: int) -> str:
    """The one sentence a reader acts on when no lane test passed.

    Two independent questions: did the lane provision anything (receipts), and
    did anything reach an assertion (outcomes). Failures lead because they are
    findings about the product; errors next because a test that threw may have
    provisioned first and "asserted nothing" would blame the wrong step.
    """
    tail = (
        " A run in this state is indistinguishable from a healthy one at the build level; "
        "read the reasons before treating any result here as evidence."
    )
    if failed:
        return (
            f"**{LANE_NOTHING_HELD}**: {failed} of {total} lane test(s) reached their "
            f"assertions and none held ({provisioned} with a provisioning receipt). Provisioning "
            f"is not the problem: the failures are findings. A whole lane failing at once "
            f"most often a single journey group is broken."
        )
    if errored:
        return (
            f"**{LANE_ERRORED_BEFORE_ASSERTING}**: {errored} of {total} lane test(s) threw before "
            f"reaching an assertion ({provisioned} with a provisioning receipt). Nothing here is a "
            f"statement about the product: read the exceptions, not the verdicts." + tail
        )
    if provisioned:
        return (
            f"**{LANE_PROVISIONED_BUT_ASSERTED_NOTHING}**: {provisioned} of {total} lane test(s) "
            f"provisioned their data and not one reached an assertion. The blocker is downstream "
            f"of provisioning: a later precondition, per test, below." + tail
        )
    return (
        f"**{LANE_PROVISIONED_NOTHING}**: {total} lane test(s) ran, none reached an assertion "
        f"and none has a provisioning receipt. The lane verified nothing." + tail
    )


@dataclass
class LaneTally:
    """Outcome per lane test, reruns collapsed onto the best attempt."""

    outcomes: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    provisioned: set[str] = field(default_factory=set)

    def observe(
        self, nodeid: str, outcome: str, reason: str = "", provisioned: bool = False
    ) -> None:
        if provisioned:
            self.provisioned.add(nodeid)
        prior = self.outcomes.get(nodeid)
        if prior is not None and _LANE_RANK.get(outcome, 0) <= _LANE_RANK.get(prior, 0):
            return
        self.outcomes[nodeid] = outcome
        self.reasons[nodeid] = flatten(reason, 200)

    def counts(self) -> Counter[str]:
        return Counter(self.outcomes.values())

    def headline(self) -> str | None:
        counts = self.counts()
        if not self.outcomes or counts["passed"]:
            return None
        provisioned = len(self.provisioned & self.outcomes.keys())
        return lane_headline(len(self.outcomes), counts["failed"], counts["errored"], provisioned)


def lane_outcome(report: pytest.TestReport) -> str | None:
    """Map one phase report to a lane outcome; ``None`` when the phase says nothing new."""
    outcome = report.outcome
    if outcome not in ("passed", "failed", "skipped"):
        return None  # e.g. a rerun attempt; the final attempt reports for itself
    if report.when == "setup":
        if outcome == "passed":
            return None
        return "skipped" if outcome == "skipped" else "errored"
    if report.when != "call":
        return None
    if outcome == "passed":
        return "passed"
    if outcome == "skipped":
        return "failed" if hasattr(report, "wasxfail") else "aborted"
    kind = dict(report.user_properties).get(FAILURE_KIND_PROPERTY)
    return "errored" if kind == "error" else "failed"


def render_status(
    defects: Mapping[str, Aggregate],
    coverage: Mapping[str, Aggregate],
    lane: LaneTally | None = None,
) -> str:
    """The Markdown status document."""
    lines = ["# Verdict status", ""]
    if defects:
        tally = Counter(a.verdict for a in defects.values())
        lines += [
            "## Tracked defects",
            "",
            f"{tally['held_empty']} of {len(defects)} tracked defect(s) could not be judged "
            f"this run.",
            "",
        ]
        if tally["held_empty"] == len(defects):
            lines += [
                f"> **{NO_DETECTOR_VERDICT}**: every detector stopped before its oracle could "
                f"judge. This run says nothing about any tracked defect.",
                "",
            ]
        lines += ["| Defect | Verdict | Meaning | Records | Detail |", "|---|---|---|---|---|"]
        for ticket, agg in defects.items():
            lines.append(
                f"| {_cell(ticket)} | {agg.verdict} | {_DEFECT_MEANING.get(agg.verdict, '')} "
                f"| {agg.records} | {_cell(agg.detail)} |"
            )
        lines.append("")
    if coverage:
        tally = Counter(a.verdict for a in coverage.values())
        lines += ["## Coverage verdicts", "", "| Outcome | Subjects | Meaning |", "|---|---|---|"]
        for outcome, meaning in _COVERAGE_MEANING.items():
            lines.append(f"| {outcome} | {tally[outcome]} | {meaning} |")
        lines.append("")
        if not tally["proved"]:
            lines += [
                f"> **{NO_PROOF}**: {len(coverage)} subject(s) reported, none of them "
                f"proved. Nothing in this run is release evidence.",
                "",
            ]
        lines += ["| Subject | Result | Notes |", "|---|---|---|"]
        for subject, agg in coverage.items():
            lines.append(f"| {_cell(subject)} | {agg.verdict} | {_cell(agg.detail)} |")
        lines.append("")
    if lane is not None and lane.outcomes:
        counts = lane.counts()
        provisioned = len(lane.provisioned & lane.outcomes.keys())
        lines += [
            "## Lane health",
            "",
            f"{len(lane.outcomes)} lane test(s): "
            + " · ".join(f"{counts[o]} {o}" for o in _LANE_RANK)
            + f"; {provisioned} with a provisioning receipt.",
            "",
        ]
        headline = lane.headline()
        if headline:
            lines += [f"> {headline}", ""]
        lines += ["| Test | Outcome | Provisioned | Reason |", "|---|---|---|---|"]
        for nodeid, outcome in sorted(lane.outcomes.items()):
            mark = "yes" if nodeid in lane.provisioned else "no"
            lines.append(
                f"| {_cell(nodeid)} | {outcome} | {mark} | {_cell(lane.reasons.get(nodeid, ''))} |"
            )
        lines.append("")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return flatten(text, 200).replace("|", "\\|")


def lane_markers() -> frozenset[str]:
    raw = os.environ.get(LANE_MARKERS_ENV)
    names = raw.split(",") if raw is not None else DEFAULT_LANE_MARKERS
    return frozenset(n.strip() for n in names if n.strip())


class VerdictStatusPlugin:
    """Tallies the lane on the controller and writes the status document at session end.

    Lane outcomes come from ``pytest_runtest_logreport``, which xdist replays
    on the controller for every worker report; verdicts come from the shared
    JSONL. Workers only tag reports (failure kind) and never write the report.
    """

    def __init__(self, config: pytest.Config) -> None:
        self._is_worker = hasattr(config, "workerinput")
        self._markers = lane_markers()
        self._lane = LaneTally()
        self._lane_ids: set[str] = set()
        self._start = 0

    def pytest_sessionstart(self, session: pytest.Session) -> None:  # noqa: ARG002
        self._start = file_size()

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            if any(item.get_closest_marker(m) for m in self._markers):
                item.user_properties.append(("verdict_lane", "1"))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]):
        report = yield
        if (
            call.when == "call"
            and report.failed
            and call.excinfo is not None
            and any(item.get_closest_marker(m) for m in self._markers)
        ):
            kind = "assertion" if call.excinfo.errisinstance(AssertionError) else "error"
            report.user_properties = [*report.user_properties, (FAILURE_KIND_PROPERTY, kind)]
        return report

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        properties = dict(report.user_properties)
        if "verdict_lane" not in properties:
            return
        outcome = lane_outcome(report)
        if outcome is None:
            return
        reason = "" if outcome == "passed" else _reason(report)
        self._lane.observe(
            report.nodeid, outcome, reason, provisioned=PROVISIONED_PROPERTY in properties
        )

    def pytest_sessionfinish(self, session: pytest.Session) -> None:  # noqa: ARG002
        if self._is_worker:
            return
        run_id = os.environ.get(RUN_ID_ENV) or None
        records = read_verdicts(run_id=run_id) if run_id else read_verdicts(start=self._start)
        defects = aggregate_defects(records)
        coverage = aggregate_coverage(records)
        if not defects and not coverage and not self._lane.outcomes:
            return
        self._log_headlines(defects, coverage)
        target = Path(os.environ.get(STATUS_FILE_ENV) or DEFAULT_STATUS_FILE)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_status(defects, coverage, self._lane), encoding="utf-8")
        except OSError as exc:
            logger.warning("[verdicts] could not write the status report: %s", exc)
            return
        logger.info("[verdicts] status report: %s", target)

    def _log_headlines(
        self, defects: Mapping[str, Aggregate], coverage: Mapping[str, Aggregate]
    ) -> None:
        if defects:
            tally = Counter(a.verdict for a in defects.values())
            logger.info(
                "[expected-red] %d tracked defect(s): %s",
                len(defects),
                ", ".join(f"{n} {v}" for v, n in sorted(tally.items())),
            )
            if tally["held_empty"] == len(defects):
                logger.warning("[expected-red] %s", NO_DETECTOR_VERDICT)
        if coverage and not any(a.verdict == "proved" for a in coverage.values()):
            logger.warning("[coverage-verdict] %s (%d subject(s))", NO_PROOF, len(coverage))
        headline = self._lane.headline()
        if headline:
            logger.warning("[lane] %s", headline.replace("**", ""))


def _reason(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    crash = getattr(longrepr, "reprcrash", None)
    if crash is not None:
        return str(crash.message)
    return str(longrepr or "")
