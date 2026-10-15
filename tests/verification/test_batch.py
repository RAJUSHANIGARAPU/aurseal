"""Tests for verification.batch, driven by a small fake batch processor.

Each fake defect mirrors a real way batch jobs misreport their work. The
harness must stay silent on the honest processor and name every lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from verification.batch import (
    BatchRunError,
    Finding,
    Outcome,
    assert_batch_truthful,
    verify_batch,
)


@dataclass
class FakeStore:
    records: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def seeded(cls) -> FakeStore:
        return cls(
            {
                "r1": {"status": "OPEN", "revision": 0},
                "r2": {"status": "OPEN", "revision": 0},
                "r3": {"status": "LOCKED", "revision": 0},  # not eligible
            }
        )

    def read(self, rid: str) -> dict:
        return self.records[rid]

    def apply(self, rid: str) -> None:
        rec = self.records[rid]
        rec["status"], rec["revision"] = "DONE", rec["revision"] + 1


class FakeProcessor:
    """Processes OPEN records; ``defect`` injects one misreporting behaviour."""

    def __init__(self, store: FakeStore, defect: str | None = None) -> None:
        self.store, self.defect = store, defect

    def run(self, ids: list[str]) -> dict[str, str]:
        report: dict[str, str] = {}
        for rid in ids:
            if self.store.read(rid)["status"] != "OPEN":
                if self.defect == "touches_skipped":
                    self.store.apply(rid)
                report[rid] = "SKIPPED"
                continue
            if self.defect == "lies_success" and rid == "r1":
                report[rid] = "SUCCESS"  # never applied
            elif self.defect == "timeout_after_commit" and rid == "r2":
                self.store.apply(rid)
                report[rid] = "FAILED"  # client gave up, server committed
            elif self.defect == "double_apply" and rid == "r1":
                self.store.apply(rid)
                self.store.apply(rid)
                report[rid] = "SUCCESS"
            elif self.defect == "drops_item" and rid == "r2":
                self.store.apply(rid)
            else:
                self.store.apply(rid)
                report[rid] = "SUCCESS"
        if self.defect == "phantom":
            report["r9"] = "SUCCESS"
        if self.defect == "crash":
            raise RuntimeError("worker died")
        return report


def revisions(before: dict, after: dict) -> int:
    return after["revision"] - before["revision"]


def _verify(defect: str | None, **kwargs):
    store = FakeStore.seeded()
    return verify_batch(
        list(store.records),
        FakeProcessor(store, defect).run,
        store.read,
        effect_count=revisions,
        **kwargs,
    )


def test_honest_processor_is_truthful():
    report = _verify(None)
    assert report.truthful, report.render()
    assert report.reported == {"r1": Outcome.SUCCESS, "r2": Outcome.SUCCESS, "r3": Outcome.SKIPPED}
    assert_batch_truthful(report)


@pytest.mark.parametrize(
    ("defect", "finding", "item"),
    [
        ("lies_success", Finding.FALSE_SUCCESS, "r1"),
        ("timeout_after_commit", Finding.FALSE_FAILURE, "r2"),
        ("double_apply", Finding.DOUBLE_PROCESSED, "r1"),
        ("touches_skipped", Finding.FALSE_SKIP, "r3"),
        ("drops_item", Finding.UNREPORTED, "r2"),
        ("phantom", Finding.UNKNOWN_ITEM, "r9"),
    ],
)
def test_each_misreport_is_detected_on_the_right_item(defect, finding, item):
    report = _verify(defect)
    assert report.of(finding) == [item], report.render()
    with pytest.raises(AssertionError, match=finding.value):
        assert_batch_truthful(report)


def test_default_effect_count_cannot_see_double_processing():
    # Documents the limit: without a counter a double apply looks like one.
    store = FakeStore.seeded()
    report = verify_batch(list(store.records), FakeProcessor(store, "double_apply").run, store.read)
    assert report.truthful


def test_ground_truth_catches_wrong_classification():
    # The processor honestly skips r3, but r3 was expected to be processed.
    report = _verify(None, expected={"r3": Outcome.SUCCESS})
    assert report.of(Finding.MISCLASSIFIED) == ["r3"]


def test_crash_mid_batch_is_judged_on_state_left_behind():
    with pytest.raises(BatchRunError) as excinfo:
        _verify("crash")
    report = excinfo.value.report
    assert report.effects == {"r1": 1, "r2": 1, "r3": 0}
    assert set(report.of(Finding.UNREPORTED)) == {"r1", "r2", "r3"}
    assert isinstance(excinfo.value.__cause__, RuntimeError)
