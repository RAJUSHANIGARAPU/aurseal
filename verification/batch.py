"""Verify a batch processor's per-item outcome report against real item state.

A batch run's summary ("98 succeeded, 2 failed") is the processor grading its
own work. This harness re-reads every item after the run and checks that the
reported outcome matches what actually happened to the item:

==================  ==========================================================
finding             meaning
==================  ==========================================================
FALSE_SUCCESS       reported SUCCESS but the item's state did not change
FALSE_FAILURE       reported FAILED but the effect was applied (e.g. a timeout
                    after the commit), so a retry would apply it twice
FALSE_SKIP          reported SKIPPED but the item changed
DOUBLE_PROCESSED    the effect was applied more than once
MISCLASSIFIED       reported outcome differs from the expected ground truth
UNREPORTED          an input item is missing from the report
UNKNOWN_ITEM        the report names an item that was not in the input
==================  ==========================================================

``effect_count(before, after)`` says how many times the effect landed on one
item. The default (0 if equal, else 1) cannot see double-processing; supply a
counter (a version delta, ledger entries, an attempt counter) to detect it.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class Finding(str, Enum):
    FALSE_SUCCESS = "FALSE_SUCCESS"
    FALSE_FAILURE = "FALSE_FAILURE"
    FALSE_SKIP = "FALSE_SKIP"
    DOUBLE_PROCESSED = "DOUBLE_PROCESSED"
    MISCLASSIFIED = "MISCLASSIFIED"
    UNREPORTED = "UNREPORTED"
    UNKNOWN_ITEM = "UNKNOWN_ITEM"


ItemId = Hashable
EffectCount = Callable[[Any, Any], int]


@dataclass(frozen=True)
class ItemFinding:
    item: ItemId
    finding: Finding
    reported: Outcome | None
    effects: int
    detail: str

    def __str__(self) -> str:
        reported = self.reported.value if self.reported else "-"
        return f"{self.item!r}: {self.finding.value} (reported {reported}) {self.detail}"


@dataclass
class BatchReport:
    reported: dict[ItemId, Outcome]
    effects: dict[ItemId, int]
    findings: list[ItemFinding] = field(default_factory=list)

    @property
    def truthful(self) -> bool:
        return not self.findings

    def of(self, finding: Finding) -> list[ItemId]:
        return [f.item for f in self.findings if f.finding is finding]

    def render(self) -> str:
        if self.truthful:
            return f"batch report truthful for {len(self.effects)} item(s)"
        lines = [f"{len(self.findings)} finding(s) across {len(self.effects)} item(s):"]
        lines += [f"  - {f}" for f in self.findings]
        return "\n".join(lines)


def changed_once(before: Any, after: Any) -> int:
    return 0 if before == after else 1


def verify_batch(
    items: Iterable[ItemId],
    run: Callable[[list[ItemId]], Mapping[ItemId, Outcome | str]],
    read_state: Callable[[ItemId], Any],
    *,
    effect_count: EffectCount = changed_once,
    expected: Mapping[ItemId, Outcome] | None = None,
) -> BatchReport:
    """Snapshot every item, run the batch, re-read every item, then classify.

    ``run`` may raise; the items are still re-read so a crash mid-batch is
    judged on what it left behind, and the exception is re-raised afterwards.
    """
    ids = list(items)
    before = {item: copy.deepcopy(read_state(item)) for item in ids}
    error: BaseException | None = None
    try:
        raw = dict(run(ids))
    except Exception as exc:  # judged below, re-raised after
        raw, error = {}, exc
    after = {item: read_state(item) for item in ids}

    report = BatchReport(
        reported={k: Outcome(v) for k, v in raw.items()},
        effects={item: effect_count(before[item], after[item]) for item in ids},
    )
    report.findings = _classify(ids, report, expected or {})
    if error is not None:
        raise BatchRunError(report) from error
    return report


def assert_batch_truthful(report: BatchReport) -> None:
    if not report.truthful:
        raise AssertionError(report.render())


class BatchRunError(RuntimeError):
    """The batch raised; ``report`` holds the state it left behind."""

    def __init__(self, report: BatchReport) -> None:
        super().__init__(f"batch run raised before reporting\n{report.render()}")
        self.report = report


def _classify(
    ids: list[ItemId], report: BatchReport, expected: Mapping[ItemId, Outcome]
) -> list[ItemFinding]:
    findings: list[ItemFinding] = []
    known = set(ids)
    for item in ids:
        effects = report.effects[item]
        outcome = report.reported.get(item)

        def add(kind: Finding, detail: str, _item=item, _o=outcome, _e=effects) -> None:
            findings.append(ItemFinding(_item, kind, _o, _e, detail))

        if outcome is None:
            add(Finding.UNREPORTED, f"effects={effects}")
        elif outcome is Outcome.SUCCESS and effects == 0:
            add(Finding.FALSE_SUCCESS, "state unchanged")
        elif outcome is Outcome.FAILED and effects > 0:
            add(Finding.FALSE_FAILURE, f"effect applied {effects}x")
        elif outcome is Outcome.SKIPPED and effects > 0:
            add(Finding.FALSE_SKIP, f"effect applied {effects}x")
        if effects > 1:
            add(Finding.DOUBLE_PROCESSED, f"effect applied {effects}x")
        if item in expected and outcome is not None and expected[item] is not outcome:
            add(Finding.MISCLASSIFIED, f"expected {expected[item].value}")
    for item in report.reported:
        if item not in known:
            findings.append(
                ItemFinding(item, Finding.UNKNOWN_ITEM, report.reported[item], 0, "not in input")
            )
    return findings
