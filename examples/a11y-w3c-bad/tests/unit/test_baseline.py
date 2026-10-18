"""The baseline rules, on hand-built scan results: no browser needed."""

from __future__ import annotations

import json

import pytest

from a11y_w3c_bad.a11y import Finding, KnownViolation, ScanResult, judge, load_baseline

pytestmark = pytest.mark.offline


def finding(rule: str, impact: str = "serious", target: str = "#x") -> Finding:
    return Finding(rule=rule, impact=impact, target=target, help="", help_url="")


def scan(*findings: Finding) -> ScanResult:
    return ScanResult(url="https://example.test/", tags=("wcag2a",), findings=findings)


KNOWN = (KnownViolation(page="home", rule="image-alt", target="#logo", issue="A11Y-12"),)


def test_a_new_serious_finding_is_new():
    verdict = judge("home", scan(finding("label")), KNOWN)
    assert [f.rule for f in verdict.new] == ["label"]


def test_a_listed_finding_is_known_not_new():
    verdict = judge("home", scan(finding("image-alt", "critical", "#logo")), KNOWN)
    assert verdict.new == ()
    assert [f.rule for f in verdict.known] == ["image-alt"]
    assert verdict.cleared == ()


def test_the_same_rule_on_another_element_is_new():
    verdict = judge("home", scan(finding("image-alt", "critical", "#banner")), KNOWN)
    assert [f.target for f in verdict.new] == ["#banner"]


def test_an_entry_only_excuses_its_own_page():
    verdict = judge("checkout", scan(finding("image-alt", "critical", "#logo")), KNOWN)
    assert [f.rule for f in verdict.new] == ["image-alt"]


@pytest.mark.parametrize("impact", ["moderate", "minor"])
def test_moderate_and_minor_findings_never_block(impact):
    assert judge("home", scan(finding("region", impact)), ()).new == ()


def test_a_listed_entry_that_no_longer_reproduces_is_cleared():
    verdict = judge("home", scan(), KNOWN)
    assert verdict.cleared == KNOWN


def test_an_entry_without_an_issue_is_refused(tmp_path):
    path = tmp_path / "a11y-baseline.json"
    entry = {"page": "home", "rule": "label", "target": "#q", "issue": " "}
    path.write_text(json.dumps({"violations": [entry]}), encoding="utf-8")
    with pytest.raises(ValueError, match="no issue"):
        load_baseline(path)


def test_a_duplicate_entry_is_refused(tmp_path):
    path = tmp_path / "a11y-baseline.json"
    entry = {"page": "home", "rule": "label", "target": "#q", "issue": "A11Y-3"}
    path.write_text(json.dumps({"violations": [entry, entry]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_baseline(path)


def test_the_shipped_baseline_loads():
    load_baseline()
