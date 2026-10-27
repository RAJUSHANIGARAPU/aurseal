"""Tests for the ownership guard and the ledger's guarded discard of system-made items."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oracles.time_window import TimestampError
from test_data.cleanup import CleanupLedger
from test_data.ownership import refusal

RUN = datetime(2031, 3, 4, 10, 0, tzinfo=timezone.utc)
SKEW = timedelta(minutes=2)


def _row(ident="m-1", key="SUB-000042", created="2031-03-04T10:05:00Z", state="NEW"):
    return {"id": ident, "subscription_number": key, "created_at": created, "status": state}


def _check(rows, item_id="m-1", key="SUB-000042"):
    return refusal(
        item_id,
        key,
        rows,
        run_started=RUN,
        skew=SKEW,
        key_of=lambda r: r["subscription_number"],
        created_of=lambda r: r["created_at"],
        state_of=lambda r: r["status"],
        initial_states={"NEW"},
    )


class TestRefusal:
    def test_our_untouched_item_is_allowed(self):
        assert _check([_row(), _row("m-9", key="SUB-000099")]) is None

    def test_within_clock_skew_before_the_run_is_allowed(self):
        assert _check([_row(created="2031-03-04T09:58:30Z")]) is None

    def test_prefix_lookalike_does_not_count_as_a_match(self):
        reason = _check([_row(key="SUB-0000421")])
        assert reason and "exactly" in reason

    def test_two_items_for_the_key_are_refused_with_ids(self):
        reason = _check([_row(), _row("m-2")])
        assert reason and "'m-1', 'm-2'" in reason

    def test_the_key_belongs_to_another_item(self):
        assert "not m-1" in _check([_row("m-7")])

    def test_created_before_the_run_is_refused(self):
        assert "before this run" in _check([_row(created="2031-03-04T09:00:00Z")])

    def test_moved_on_is_refused(self):
        assert "SENT" in _check([_row(state="SENT")])

    def test_unreadable_time_is_refused_not_guessed(self):
        assert "unreadable" in _check([_row(created="2031-03-04T10:05:00")])

    def test_empty_key_and_naive_run_start(self):
        assert "empty key" in _check([_row(key="")], key="")
        with pytest.raises(TimestampError):
            refusal(
                "m",
                "k",
                [],
                run_started=datetime(2031, 1, 1),
                skew=SKEW,
                key_of=str,
                created_of=str,
                state_of=str,
                initial_states=(),
            )


class TestRecordOwned:
    def test_discards_when_the_guard_allows(self):
        ledger, gone = CleanupLedger(), []
        ledger.record_owned("message", "m-1", lambda: gone.append("m-1"), lambda: None)
        report = ledger.discard_all()
        assert gone == ["m-1"] and report.discarded == ["message:m-1"] and report.clean

    def test_refusal_logs_and_keeps_the_item(self, caplog):
        ledger, gone, reads = CleanupLedger(), [], []

        def guard():
            reads.append("fresh read")
            return _check([_row(state="SENT")])

        ledger.record_owned("message", "m-1", lambda: gone.append("m-1"), guard)
        report = ledger.discard_all()
        assert gone == [] and reads == ["fresh read"]
        assert report.refused and "SENT" in report.refused[0]
        assert "not discarding message:m-1" in caplog.text

    def test_guard_runs_at_teardown_not_at_record_time(self):
        ledger, state = CleanupLedger(), {"status": "NEW"}
        ledger.record_owned(
            "message",
            "m-1",
            lambda: None,
            lambda: _check([_row(state=state["status"])]),
        )
        state["status"] = "SENT"
        assert ledger.discard_all().refused

    def test_a_raising_guard_is_a_failed_cleanup(self):
        ledger = CleanupLedger()
        ledger.record_owned("message", "m-1", lambda: None, lambda: 1 / 0)
        assert ledger.discard_all().failed == ["message:m-1"]
