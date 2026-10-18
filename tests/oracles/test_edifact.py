"""Tests for oracles.edifact (invented interchanges)."""

from __future__ import annotations

import pytest

from oracles.edifact import EdifactError, Segment, ServiceChars, group_by, parse_interchange

INTERCHANGE = (
    "UNA:+.? '"
    "UNB+UNOC:3+SENDER01+RECEIVER01+310504:1000+REF001'\n"
    "UNH+M1+ORDERS:D:96A:UN'\n"
    "BGM+220+ORD-42'\n"
    "ITM+1'FTX+Price 10?+2?:5 ?? ok'\n"
    "ITM+2'QTY+21:3'\n"
    "UNT+7+M1'\n"
    "UNH+M2+ORDERS:D:96A:UN'BGM+220+ORD-43'UNT+3+M2'\n"
    "UNZ+2+REF001'"
)


def test_segments_elements_and_components():
    ic = parse_interchange(INTERCHANGE)
    assert ic.service == ServiceChars()
    unb = ic.segments[0]
    assert unb.tag == "UNB" and unb.component(0, 1) == "3" and unb.element(4) == "REF001"
    ftx = next(s for s in ic.segments if s.tag == "FTX")
    assert ftx.element(0) == "Price 10+2:5 ? ok"  # release character honoured
    qty = next(s for s in ic.segments if s.tag == "QTY")
    assert qty.elements == (("21", "3"),)  # components split, not left inside the value
    assert qty.element(5, "none") == "none"


def test_messages_and_lookup():
    ic = parse_interchange(INTERCHANGE)
    assert [m.reference for m in ic.messages] == ["M1", "M2"]
    assert ic.messages[0].message_type == "ORDERS"
    m = ic.find_message(lambda msg: msg.first("BGM").element(1) == "ORD-43")
    assert m is not None and m.reference == "M2"
    assert ic.find_message(lambda msg: False) is None
    assert [s.tag for s in ic.messages[0].find("ITM")] == ["ITM", "ITM"]


def test_complete_interchange_has_no_problems():
    assert parse_interchange(INTERCHANGE).completeness() == []


def test_completeness_problems():
    broken = (
        "UNB+UNOC:3+S+R+310504:1000+REF001'"
        "UNH+M1+ORDERS'BGM+220'UNT+9+MX'"
        "UNH+M2+ORDERS'BGM+220'"
        "UNZ+5+OTHER'"
    )
    problems = parse_interchange(broken).completeness()
    assert problems == [
        "message M1: UNT declares 9 segments, found 3",
        "message M1: UNT reference 'MX' differs from UNH",
        "message M2: no UNT",
        "UNZ declares 5 messages, found 2",
        "UNZ reference 'OTHER' differs from UNB 'REF001'",
    ]
    assert parse_interchange("UNB+X+S+R+D+REF'").completeness() == ["UNB without UNZ"]


def test_custom_service_characters():
    ic = parse_interchange("UNA|*,# ~UNH*M1*ORDERS~FTX*a#*b|c*d~UNT*3*M1~")
    ftx = ic.messages[0].first("FTX")
    assert ftx.elements == (("a*b", "c"), ("d",))
    assert ic.service.decimal == ","


def test_group_by_start_tag():
    msg = parse_interchange(INTERCHANGE).messages[0]
    groups = group_by(msg.segments, "ITM")
    assert [[s.tag for s in g] for g in groups] == [["ITM", "FTX"], ["ITM", "QTY"]]


def test_errors():
    with pytest.raises(EdifactError):
        parse_interchange("UNA:+")
    with pytest.raises(EdifactError, match="no valid tag"):
        parse_interchange("UNH+1'+bad'")


def test_segment_str_round_trip_without_escapes():
    assert str(Segment("QTY", (("21", "3"),))) == "QTY+21:3"
