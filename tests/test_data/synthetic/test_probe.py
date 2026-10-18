"""Probe verdicts: the label decides what a correct answer is; a 5xx never is one."""

from __future__ import annotations

import pytest

from test_data.synthetic.generator import SyntheticRecord
from test_data.synthetic.probe import FAIL, PASS, UNREACHABLE, judge, probe, summarise


def _record(broken=None):
    return SyntheticRecord({"a": 1}, "invalid" if broken else "valid", broken, 7, 0, "s")


@pytest.mark.parametrize(
    "expected,status,verdict",
    [
        ("accept", 201, PASS),
        ("accept", 422, FAIL),
        ("reject", 422, PASS),
        ("reject", 200, FAIL),
        ("reject", 500, FAIL),
        ("accept", 503, FAIL),
        ("accept", 302, FAIL),
        ("reject", None, UNREACHABLE),
    ],
)
def test_judge(expected, status, verdict):
    assert judge(expected, status)[0] == verdict


class Http:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.bodies = []

    def post(self, path, json=None):
        self.bodies.append((path, json))
        status = self.statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return type("R", (), {"status": status})()


def test_probe_posts_values_and_judges_each():
    http = Http([201, 201, 500])
    results = probe("/api/x", http, [_record(), _record("a.max"), _record("a.min")])
    assert [r.verdict for r in results] == [PASS, FAIL, FAIL]
    assert http.bodies[0] == ("/api/x", {"a": 1})
    summary = summarise(results)
    assert summary["failed"] == 2 and summary["passed"] == 1
    assert {f["detail"] for f in summary["failures"]} == {
        "invalid record accepted (201)",
        "server error 500",
    }


def test_transport_error_is_a_result_not_a_crash():
    results = probe("/api/x", Http([ConnectionError("refused")]), [_record()])
    assert results[0].verdict == UNREACHABLE and results[0].status is None


def test_metric_row_shape():
    row = probe("/x", Http([422]), [_record("a.max")])[0].metric_row("thing")
    assert row == {
        "profile": "thing",
        "mode": "invalid",
        "seed": 7,
        "record_index": 0,
        "strategy": "s",
        "broken_constraint": "a.max",
        "expected": "reject",
        "observed_status": 422,
        "verdict": "pass",
    }
