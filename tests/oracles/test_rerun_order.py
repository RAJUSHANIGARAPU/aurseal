"""Retry of a partly failed ordered job: proved, violation, not exercised, not distinguishable."""

from __future__ import annotations

import pytest

from oracles.rerun_order import ItemState, Kind, Run, judge, report
from verification.evidence_verdict import EvidenceVerdict

RANKS = {"invoice": 1, "statement": 2, "notice": 3}


def rank(item: ItemState) -> int:
    return RANKS[item.category]


def new_transaction(before: ItemState, after: ItemState) -> bool:
    old = (before.detail or {}).get("tx")
    new = (after.detail or {}).get("tx")
    return new is not None and new != old


def item(id_, category, done, status=None, stamp=None, tx=None):
    status = status or ("SENT" if done else "FAILED")
    return ItemState(id_, category, done, status, stamp, {"tx": tx} if tx else None)


BEFORE = {
    "f1": item("f1", "invoice", True, stamp="t0", tx="tx-1"),
    "f2": item("f2", "statement", False),
    "f3": item("f3", "notice", False),
}


def run(*items, run_id="r2"):
    return [Run(run_id, tuple(items))]


def kinds(findings):
    return [f.kind for f in findings]


def test_retry_that_sends_the_first_open_item_and_stops_is_proved():
    after = run(
        BEFORE["f1"],
        item("f2", "statement", True, stamp="t1", tx="tx-2"),
        item("f3", "notice", False, status="FAILED"),
    )
    findings = judge(BEFORE, after, rank, new_transaction)
    assert kinds(findings) == [Kind.PROVED] and findings[0].item == "f2"


def test_retry_that_stops_on_the_first_open_item_yet_touches_a_later_one_is_a_violation():
    after = run(
        BEFORE["f1"],
        item("f2", "statement", False, status="FAILED", stamp="t1"),
        item("f3", "notice", True, stamp="t1", tx="tx-3"),
    )
    findings = judge(BEFORE, after, rank, new_transaction)
    assert kinds(findings) == [Kind.VIOLATION] and findings[0].item == "f3"


def test_fewer_than_two_open_items_cannot_show_the_rule():
    before = {**BEFORE, "f2": item("f2", "statement", True, stamp="t0", tx="tx-2")}
    after = run(before["f1"], before["f2"], item("f3", "notice", True, stamp="t1", tx="tx-3"))
    assert kinds(judge(before, after, rank, new_transaction)) == [Kind.NOT_EXERCISED]


def test_no_run_is_not_exercised():
    assert kinds(judge(BEFORE, [], rank, new_transaction)) == [Kind.NOT_EXERCISED]


def test_more_than_one_run_is_not_distinguishable():
    after = run(*BEFORE.values()) + run(*BEFORE.values(), run_id="r3")
    assert kinds(judge(BEFORE, after, rank, new_transaction)) == [Kind.NOT_DISTINGUISHABLE]


def test_item_unknown_before_is_not_distinguishable():
    after = run(*BEFORE.values(), item("f9", "notice", True))
    findings = judge(BEFORE, after, rank, new_transaction)
    assert kinds(findings) == [Kind.NOT_DISTINGUISHABLE] and findings[0].item == "f9"


def test_done_before_but_not_after_is_not_the_same_run():
    after = run(item("f1", "invoice", False), BEFORE["f2"], BEFORE["f3"])
    findings = judge(BEFORE, after, rank, new_transaction)
    assert kinds(findings) == [Kind.NOT_DISTINGUISHABLE] and "same run" in findings[0].text


def test_a_change_without_side_channel_evidence_is_not_distinguishable():
    after = run(BEFORE["f1"], item("f2", "statement", True, stamp="t1"), BEFORE["f3"])
    findings = judge(BEFORE, after, rank, new_transaction)
    assert kinds(findings) == [Kind.NOT_DISTINGUISHABLE] and "evidence" in findings[0].text


def test_nothing_changed_is_not_distinguishable():
    after = run(*BEFORE.values())
    assert kinds(judge(BEFORE, after, rank, new_transaction)) == [Kind.NOT_DISTINGUISHABLE]


def test_unknown_category_fails_loudly():
    before = {**BEFORE, "f4": item("f4", "receipt", False)}
    with pytest.raises(ValueError, match="no rank for category 'receipt'"):
        judge(before, run(*BEFORE.values()), rank, new_transaction)


def test_report_maps_proved_violation_and_abstention(monkeypatch, tmp_path):
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.delenv("COVERAGE_VERDICT_REQUIRED", raising=False)
    coverage = EvidenceVerdict("retry stops at the first failed file")
    proved = judge(
        BEFORE,
        run(BEFORE["f1"], item("f2", "statement", True, stamp="t1", tx="tx-2"), BEFORE["f3"]),
        rank,
        new_transaction,
    )
    report(proved, coverage)
    with pytest.raises(AssertionError, match="VIOLATION f3"):
        report(
            judge(
                BEFORE,
                run(BEFORE["f1"], BEFORE["f2"], item("f3", "notice", True, "SENT", "t1", "x")),
                rank,
                new_transaction,
            ),
            coverage,
        )
    with pytest.raises(pytest.skip.Exception, match="NO VERDICT"):
        report(judge(BEFORE, [], rank, new_transaction), coverage)
