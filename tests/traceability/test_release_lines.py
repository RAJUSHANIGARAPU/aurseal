"""Detectors for a fix in another release line neither block nor count as no verdict."""

from __future__ import annotations

import json
import textwrap

import pytest

from tests.traceability._release import M, decide, proved_run
from tests.traceability._runs import T, red
from traceability.release import OUT_OF_SCOPE, ReleaseVerdict, decision_json
from verification.known_defects_manifest import fix_lines, parse_manifest

pytest_plugins = ["pytester"]


def _run_with(ticket: str, verdict: str):
    run = proved_run()
    run.tests.append(T(M + "d", "failed"))
    run.verdicts.append(red("r1", M + "d", ticket, verdict))
    return run


def test_a_blocking_detector_for_another_line_is_out_of_scope(tmp_path):
    run = _run_with("BUG-9", "signature_mismatch")
    d = decide(tmp_path, [run], release_line="2.3", fix_lines={"BUG-9": "2.4"})
    assert d.verdict is ReleaseVerdict.GO
    (reason,) = [r for r in d.reasons if r.criterion == "detector:BUG-9"]
    assert reason.severity == OUT_OF_SCOPE and "release line 2.4, not 2.3" in reason.message
    assert "### Out of scope (1)" in d.to_markdown()
    decision_json(d)  # the schema accepts the new severity


def test_an_unexercised_detector_for_another_line_is_not_no_verdict(tmp_path):
    run = _run_with("BUG-9", "held_empty")
    d = decide(tmp_path, [run], release_line="2.3", fix_lines={"BUG-9": "2.4"})
    assert d.verdict is ReleaseVerdict.GO


def test_same_line_and_unscoped_defects_still_count(tmp_path):
    run = _run_with("BUG-9", "signature_mismatch")
    same = decide(tmp_path / "a", [run], release_line="2.4", fix_lines={"BUG-9": "2.4"})
    unscoped = decide(tmp_path / "b", [run], release_line="2.3", fix_lines={})
    without_line = decide(tmp_path / "c", [run], fix_lines={"BUG-9": "2.4"})
    assert {same.verdict, unscoped.verdict, without_line.verdict} == {ReleaseVerdict.NO_GO}


def test_the_hash_is_unchanged_without_a_release_line(tmp_path):
    plain = decide(tmp_path / "a", [proved_run()])
    with_map = decide(tmp_path / "b", [proved_run()], fix_lines={"BUG-9": "2.4"})
    scoped = decide(tmp_path / "c", [proved_run()], release_line="2.3")
    assert plain.inputs_hash == with_map.inputs_hash != scoped.inputs_hash


MANIFEST = [
    {
        "template": "monthly-invoice",
        "open_issues": [
            {"issue": "BUG-9", "summary": "rounding", "finding": "TOTAL", "fix_line": "2.4"},
            {"issue": "BUG-10", "summary": "wording"},
        ],
    },
    {"template": "credit-note", "open_issues": [{"issue": "BUG-9", "summary": "rounding"}]},
]


def test_manifest_fix_lines():
    entries = parse_manifest(MANIFEST)
    assert entries[0].open_issues[0].fix_line == "2.4"
    assert fix_lines(entries) == {"BUG-9": "2.4"}
    clash = json.loads(json.dumps(MANIFEST))
    clash[1]["open_issues"][0]["fix_line"] = "2.5"
    with pytest.raises(ValueError, match="two fix lines"):
        fix_lines(parse_manifest(clash))


SUITE = textwrap.dedent(
    """
    import pytest

    @pytest.mark.known_defect("BUG-9", fix_line="2.4", raises=AssertionError)
    def test_fixed_in_next_line():
        assert False

    @pytest.mark.known_defect("BUG-10", fix_line="2.3", raises=AssertionError)
    def test_fixed_in_this_line():
        assert False

    def test_plain():
        pass
    """
)


@pytest.fixture
def suite(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("VERDICT_STATUS_FILE", str(tmp_path / "status.md"))
    monkeypatch.delenv("RELEASE_LINE", raising=False)
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    pytester.makepyfile(test_suite=SUITE)
    return pytester


def test_release_line_deselects_detectors_of_other_lines(suite, monkeypatch):
    monkeypatch.setenv("RELEASE_LINE", "2.3")
    result = suite.runpytest("-p", "verification.pytest_plugin", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, xfailed=1, deselected=1)


def test_without_a_release_line_every_detector_runs(suite):
    result = suite.runpytest("-p", "verification.pytest_plugin", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, xfailed=2)
