"""EvidenceVerdict outcomes and the per-environment fixed/expected-red reading."""

from __future__ import annotations

import pytest

from verification.evidence_verdict import EvidenceVerdict
from verification.fix_scope import fix_scope_envs, judge
from verification.verdicts import RedVerdict, read_verdicts

SIGNATURE = r"BUG-142: discount was not applied to the order total"


def broken() -> None:
    raise AssertionError("BUG-142: discount was not applied to the order total")


def rows() -> list[tuple[str, str]]:
    return [(r["kind"], r["verdict"]) for r in read_verdicts()]


# --- coverage verdicts --------------------------------------------------------


def test_each_outcome_is_recorded_against_its_subject():
    verdict = EvidenceVerdict("REQ-12 order totals", ticket="BUG-142")
    verdict.proved("total 12.40 = sum of 3 lines on order 7")
    verdict.recorded("tax 2.10, no offline oracle")
    verdict.held_empty("cross-currency check: single-currency order")
    entries = read_verdicts()
    assert [e["verdict"] for e in entries] == ["proved", "recorded", "held_empty"]
    assert {e["subject"] for e in entries} == {"REQ-12 order totals"}
    assert {e["ticket"] for e in entries} == {"BUG-142"}
    assert entries[0]["detail"] == "total 12.40 = sum of 3 lines on order 7"


def test_no_verdict_skips_by_default():
    with pytest.raises(pytest.skip.Exception, match="NO VERDICT"):
        EvidenceVerdict("REQ-12").no_verdict("empty shared pool")
    assert rows() == [("coverage", "abstained")]


@pytest.mark.parametrize("value", ["true", "1", "YES"])
def test_no_verdict_fails_when_a_verdict_is_required(monkeypatch, value):
    monkeypatch.setenv("COVERAGE_VERDICT_REQUIRED", value)
    with pytest.raises(pytest.fail.Exception, match="abstention is a failure"):
        EvidenceVerdict("REQ-12").no_verdict("empty shared pool")
    assert rows() == [("coverage", "abstained")]


def test_proved_needs_evidence_and_a_subject():
    with pytest.raises(ValueError):
        EvidenceVerdict("REQ-12").proved("  ")
    with pytest.raises(ValueError):
        EvidenceVerdict(" ")
    assert rows() == []


# --- fixed on one environment, expected-red elsewhere --------------------------


def test_fix_scope_envs_reads_the_variable_or_the_default(monkeypatch):
    assert fix_scope_envs("FIX_ENVS", "staging, qa") == {"staging", "qa"}
    monkeypatch.setenv("FIX_ENVS", "prod,,")
    assert fix_scope_envs("FIX_ENVS", "staging") == {"prod"}
    monkeypatch.setenv("FIX_ENVS", "")
    assert fix_scope_envs("FIX_ENVS", "staging") == frozenset()


def _judge(env: str, oracle, control=lambda: None):
    return judge(
        env,
        {"staging"},
        "BUG-142",
        SIGNATURE,
        coverage=EvidenceVerdict("BUG-142 discount"),
        control=control,
        oracle=oracle,
        evidence=lambda: "discount applied on order 7",
    )


def test_elsewhere_the_oracle_is_a_detector():
    assert _judge("qa", broken) is RedVerdict.REPRODUCES
    with pytest.raises(AssertionError, match="CLEARED"):
        _judge("qa", lambda: None)
    assert rows() == [("expected_red", "reproduces"), ("expected_red", "cleared")]


def test_where_fixed_a_pass_is_proved_and_cleared():
    assert _judge("staging", lambda: None) is RedVerdict.CLEARED
    assert rows() == [("coverage", "proved"), ("expected_red", "cleared")]


def test_where_fixed_a_closed_defect_reads_closed_pass(monkeypatch):
    monkeypatch.setenv("KNOWN_DEFECTS_CLOSED", "BUG-142")
    assert _judge("staging", lambda: None) is RedVerdict.CLOSED_PASS


def test_where_fixed_a_failing_oracle_fails_plainly_and_records_the_defect():
    with pytest.raises(AssertionError, match="discount was not applied"):
        _judge("staging", broken)
    assert rows() == [("expected_red", "reproduces")]

    def other() -> None:
        raise AssertionError("order could not be loaded")

    with pytest.raises(AssertionError, match="could not be loaded"):
        _judge("staging", other)
    assert rows()[-1] == ("expected_red", "signature_mismatch")


@pytest.mark.parametrize("env", ["staging", "qa"])
def test_a_failing_control_is_an_abstention_in_both_readings(env):
    def control() -> None:
        raise AssertionError("order read back empty")

    with pytest.raises(pytest.skip.Exception):
        _judge(env, broken, control=control)
    assert rows()[-1][1] in {"abstained", "held_empty"}
    assert not any(v in {"proved", "reproduces", "cleared"} for _, v in rows())


def test_where_fixed_a_loose_signature_is_still_rejected():
    with pytest.raises(ValueError):
        judge(
            "staging",
            {"staging"},
            "BUG-142",
            "BUG-142",
            coverage=EvidenceVerdict("x"),
            control=lambda: None,
            oracle=lambda: None,
            evidence=lambda: "y",
        )
