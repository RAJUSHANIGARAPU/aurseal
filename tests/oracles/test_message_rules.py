"""Message completeness rules over an invented segment message."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from oracles import message_rules as mr
from oracles.edifact import parse_interchange

GOOD = (
    "HDR+M1+BOOKING'SUB+S-1+1'CUS+C-1+P1'"
    "AMT+NET+10.00'AMT+TAX+2.00'AMT+FEE+0.50'AMT+TOT+12.50'END+8'"
)
KINDS = {"PERSON": "P1", "ORGANISATION": "O1"}


@dataclass
class Payload:
    segments: list
    xml: str | None = "<doc><a/></doc>"


def rules(kind="PERSON"):
    amt = lambda q: mr.segment_value("AMT", 1, qualifier=q)  # noqa: E731
    return [
        mr.totals_balance(
            "total balances", amt("TOT"), [amt("NET"), amt("TAX"), amt("FEE")], reason="R1"
        ),
        mr.required_group("customer group", "CUS", reason="R2"),
        mr.code_mapped("customer kind", mr.segment_value("CUS", 1), kind, KINDS, reason="R3"),
        mr.declared_count("segment count", "END", reason="R4"),
        mr.xml_well_formed("document xml", lambda p: p.xml, reason="R5"),
    ]


def payload(text=GOOD, xml="<doc><a/></doc>"):
    return Payload(parse_interchange(text).segments, xml)


def test_complete_message_holds_every_rule():
    results = mr.evaluate(payload(), rules())
    assert [r.rule for r in results] == [
        "total balances",
        "customer group",
        "customer kind",
        "segment count",
        "document xml",
    ]
    assert all(r.holds for r in results) and mr.violations(results) == []


def test_total_that_does_not_balance_names_parts_and_reason():
    (line,) = mr.violations(mr.evaluate(payload(GOOD.replace("TOT+12.50", "TOT+12.40")), rules()))
    assert line.startswith("total balances: AMT+NET[1] + AMT+TAX[1] + AMT+FEE[1] = 12.50")
    assert line.endswith("but total is 12.40 (R1)")


def test_missing_amount_is_reported_not_counted_as_zero():
    text = GOOD.replace("AMT+FEE+0.50'", "AMT+FEE+x'")
    problems = mr.violations(mr.evaluate(payload(text), rules()[:1]))
    assert problems == ["total balances: missing or non-numeric: ['AMT+FEE[1]'] (R1)"]


def test_missing_group_wrong_code_and_wrong_count():
    text = GOOD.replace("CUS+C-1+P1'", "")
    found = mr.violations(mr.evaluate(payload(text), rules()))
    assert "customer group: 0 CUS segment(s), need 1 (R2)" in found
    assert "customer kind: 'PERSON' sent as None, expected 'P1' (R3)" in found
    assert "segment count: declares 8, has 7 (R4)" in found
    unknown = mr.violations(mr.evaluate(payload(), rules(kind="TRUST")))
    assert unknown == [
        "customer kind: no code mapped for 'TRUST' (known: ['ORGANISATION', 'PERSON']) (R3)"
    ]


@pytest.mark.parametrize(
    ("xml", "problem"),
    [
        (None, "XML part is absent or empty"),
        ("<doc><a></doc>", "XML part is not well-formed"),
    ],
)
def test_xml_part(xml, problem):
    (line,) = mr.violations(mr.evaluate(payload(xml=xml), rules()[4:]))
    assert problem in line


def test_declaration_can_be_refused_and_a_crashing_rule_is_a_finding():
    strict = mr.xml_well_formed("x", lambda p: p.xml, reason="r", allow_declaration=False)
    found = mr.violations(mr.evaluate(payload(xml='<?xml version="1.0"?><d/>'), [strict]))
    assert found == ["x: XML part carries a declaration the receiver does not accept (r)"]
    boom = mr.Rule("boom", "r", lambda s: 1 / 0)
    (result,) = mr.evaluate(payload(), [boom])
    assert not result.holds and "ZeroDivisionError" in result.problems[0]


def test_tolerance_and_plain_segment_lists():
    segments = parse_interchange(GOOD.replace("TOT+12.50", "TOT+12.51")).segments
    amt = lambda q: mr.segment_value("AMT", 1, qualifier=q)  # noqa: E731
    loose = mr.totals_balance(
        "t", amt("TOT"), [amt("NET"), amt("TAX"), amt("FEE")], reason="r", tolerance=Decimal("0.01")
    )
    assert mr.violations(mr.evaluate(segments, [loose])) == []
