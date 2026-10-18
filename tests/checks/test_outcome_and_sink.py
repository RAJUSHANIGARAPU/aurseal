"""The shared outcome mapping and the metric sink every check engine uses."""

from __future__ import annotations

import json

import pytest

from checks._outcome import CheckOutcome, exit_code, to_plugin_status
from checks._sink import JsonlSink, MemorySink, metric_row, schema_for, validate_row


class TestStatusMapping:
    def test_measured_and_held_is_pass(self):
        assert to_plugin_status(CheckOutcome.ok("k", "fine")) == "pass"

    def test_measured_and_breached_is_fail(self):
        assert to_plugin_status(CheckOutcome.breached("k", "crossed")) == "fail"

    def test_not_measured_is_unknown_never_pass(self):
        assert to_plugin_status(CheckOutcome.unknown("k", "tool missing")) == "unknown"

    def test_a_passed_flag_without_a_measurement_is_still_unknown(self):
        """``passed`` is only read when something was measured."""
        outcome = CheckOutcome(kind="k", measured=False, passed=True, reason="claims success")
        assert to_plugin_status(outcome) == "unknown"

    def test_harness_error_is_error(self):
        assert to_plugin_status(CheckOutcome.error("k", "bad command line")) == "error"

    def test_dry_run_and_baseline_runs_are_skip(self):
        assert to_plugin_status(CheckOutcome.skipped("k", "dry")) == "skip"
        taken = CheckOutcome.skipped("k", "baseline", baseline_taken=True)
        assert to_plugin_status(taken) == "skip"
        assert taken.summary()["baseline_taken"] is True

    def test_warn_is_never_produced(self):
        outcomes = [
            CheckOutcome.ok("k", ""),
            CheckOutcome.breached("k", ""),
            CheckOutcome.unknown("k", ""),
            CheckOutcome.error("k", ""),
            CheckOutcome.skipped("k", ""),
        ]
        assert "warn" not in {to_plugin_status(o) for o in outcomes}

    @pytest.mark.parametrize(
        ("outcome", "code"),
        [
            (CheckOutcome.ok("k", ""), 0),
            (CheckOutcome.skipped("k", ""), 0),
            (CheckOutcome.breached("k", ""), 1),
            (CheckOutcome.unknown("k", ""), 2),
            (CheckOutcome.error("k", ""), 2),
        ],
    )
    def test_cli_exit_code(self, outcome, code):
        assert exit_code(outcome) == code


def _load_row(**over):
    row = metric_row(
        run_id="run-1",
        target="http://127.0.0.1:1/api/health",
        thresholds_json="[]",
        breached_json="[]",
        tool="k6",
    )
    row.update(over)
    return row


class TestSink:
    def test_row_carries_the_common_columns(self, monkeypatch):
        monkeypatch.setenv("TEST_RUN_ID", "ci-77")
        row = metric_row(target="x")
        assert row["run_id"] == "ci-77"
        assert row["id"] and row["created_at"] and row["nodeid"] is None

    def test_jsonl_sink_appends_validated_rows(self, tmp_path):
        sink = JsonlSink(tmp_path)
        sink.write("metric_load_run", [_load_row()])
        sink.write("metric_load_run", [_load_row(requests=3)])
        rows = sink.read("metric_load_run")
        assert [r.get("requests") for r in rows] == [None, 3]
        assert json.loads(sink.path("metric_load_run").read_text().splitlines()[0])["tool"] == "k6"

    def test_a_row_outside_the_schema_is_refused_and_nothing_is_written(self, tmp_path):
        sink = JsonlSink(tmp_path)
        with pytest.raises(ValueError, match="schema"):
            sink.write("metric_load_run", [_load_row(), _load_row(error_rate=7)])
        assert sink.read("metric_load_run") == []

    def test_an_unknown_table_is_refused(self):
        with pytest.raises(KeyError):
            schema_for("metric_nothing")

    def test_memory_sink_validates_too(self):
        sink = MemorySink()
        with pytest.raises(ValueError):
            sink.write("metric_load_run", [_load_row(surprise=1)])
        sink.write("metric_load_run", [_load_row()])
        assert len(sink.rows["metric_load_run"]) == 1

    def test_validate_row_accepts_a_complete_row(self):
        validate_row("metric_load_run", _load_row(p95_ms=1.5, error_rate=0.0))
