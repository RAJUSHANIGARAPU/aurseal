"""The post-run SLO check: it must fail every run that misses a budget."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from perf_locust_quickpizza.slo import (
    PROFILES,
    SLO_FILE,
    Budget,
    Measured,
    breaches,
    load_budgets,
    main,
    read_stats,
)

pytestmark = pytest.mark.offline

BUDGET = Budget(p95_ms=500, error_rate=0.01)
HEADER = (
    "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
    "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
    "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%"
)


def _stats(tmp_path: Path, requests: int, failures: int, p95: str) -> Path:
    """A stats file in the shape Locust writes: one row per endpoint, then Aggregated."""
    rows = [
        HEADER,
        f"GET,home,{requests},{failures},40,45,10,900,512,9.5,0,40,45,50,55,80,{p95},"
        f"{p95},{p95},{p95},{p95},{p95}",
        f",Aggregated,{requests},{failures},40,45,10,900,512,9.5,0,40,45,50,55,80,{p95},"
        f"{p95},{p95},{p95},{p95},{p95}",
    ]
    path = tmp_path / "run_stats.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_a_run_inside_every_budget_passes():
    assert breaches(Measured(requests=1000, failures=5, p95_ms=480), BUDGET) == []


def test_slo_evaluator_fails_when_p95_exceeds_budget():
    found = breaches(Measured(requests=1000, failures=0, p95_ms=510), BUDGET)
    assert len(found) == 1
    assert "p95" in found[0]


def test_slo_evaluator_fails_when_error_rate_exceeds_budget():
    found = breaches(Measured(requests=1000, failures=11, p95_ms=100), BUDGET)
    assert len(found) == 1
    assert "error rate" in found[0]


def test_a_run_that_measured_nothing_cannot_pass():
    assert breaches(Measured(requests=0, failures=0, p95_ms=None), BUDGET)


def test_the_aggregated_row_is_the_one_read(tmp_path):
    measured = read_stats(_stats(tmp_path, requests=200, failures=3, p95="610"))
    assert measured == Measured(requests=200, failures=3, p95_ms=610.0)
    assert measured.error_rate == pytest.approx(0.015)


def test_a_file_without_an_aggregated_row_is_rejected(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text(HEADER + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Aggregated"):
        read_stats(path)


@pytest.mark.parametrize(
    ("p95", "code"),
    [
        pytest.param("120", 0, id="inside-budget-exits-0"),
        pytest.param("99999", 1, id="breach-exits-1"),
    ],
)
def test_command_line_exit_code_is_the_verdict(tmp_path, capsys, p95, code):
    assert main(["load", str(_stats(tmp_path, requests=100, failures=0, p95=p95))]) == code
    assert ("BREACHED" in capsys.readouterr().out) is bool(code)


def test_the_committed_slo_file_has_a_budget_for_every_profile():
    assert set(load_budgets()) == set(PROFILES)


def test_load_budget_is_no_looser_than_stress():
    budgets = load_budgets()
    assert budgets["load"].p95_ms <= budgets["stress"].p95_ms


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param({"p95_ms": 0}, "greater than 0", id="zero-latency-budget"),
        pytest.param({"error_rate": 0.2}, "less than or equal", id="error-rate-over-5pct"),
        pytest.param({"p59_ms": 500}, "Extra inputs", id="typo-in-a-key"),
    ],
)
def test_a_meaningless_budget_is_rejected(tmp_path, change, message):
    budgets: dict[str, Any] = json.loads(SLO_FILE.read_text(encoding="utf-8"))
    budgets["load"] = {**budgets["load"], **change}
    path = tmp_path / "slo.json"
    path.write_text(json.dumps(budgets), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_budgets(path)
