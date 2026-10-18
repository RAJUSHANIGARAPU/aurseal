"""JUnit and verdict-line ingestion: any framework's XML, tolerant JSONL."""

from __future__ import annotations

import json

import pytest

from qaplatform.errors import BadRequest, TooLarge
from qaplatform.ingest.junit import parse_junit
from qaplatform.ingest.verdicts import parse_verdict_lines
from tests.qaplatform._helpers import finish, new_run

PYTEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2">
  <testcase classname="tests.test_login" name="test_ok" time="0.120"/>
  <testcase classname="tests.test_login" name="test_bad" time="0.300">
    <failure message="assert 1 == 2">trace</failure>
  </testcase>
</testsuite></testsuites>"""

JUNIT5_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.example.InvoiceTest" tests="3" timestamp="2026-01-05T10:00:00">
  <testcase classname="com.example.InvoiceTest" name="totalsIncludeTax()" time="0.4">
    <properties>
      <property name="requirement" value="REQ-12"/>
      <property name="requirement" value="REQ-13"/>
      <property name="defect" value="BUG-142"/>
    </properties>
  </testcase>
  <testcase classname="com.example.InvoiceTest" name="refund()" time="0.1">
    <error message="NullPointerException" type="java.lang.NullPointerException">at x</error>
  </testcase>
  <testcase classname="com.example.InvoiceTest" name="later()" time="0">
    <skipped message="disabled"/>
  </testcase>
</testsuite>"""

JEST_XML = b"""<testsuites name="jest tests">
  <testsuite name="login" tests="1">
    <testcase classname="login rejects bad password" name="login rejects bad password" time="0.01">
      <properties><property name="requirement" value="not-a-key"/></properties>
      <system-out>[[ATTACHMENT|reports/login.png]]</system-out>
    </testcase>
  </testsuite>
</testsuites>"""


def test_pytest_xml_maps_back_to_node_ids():
    items, rejected = parse_junit(PYTEST_XML)
    assert {i["test_id"]: i["outcome"] for i in items} == {
        "tests/test_login.py::test_ok": "passed",
        "tests/test_login.py::test_bad": "failed",
    }
    assert rejected == []


def test_junit5_style_properties_become_links():
    items, _ = parse_junit(JUNIT5_XML)
    by_id = {i["test_id"]: i for i in items}
    first = by_id["com.example.InvoiceTest::totalsIncludeTax()"]
    assert first["requirements"] == ["REQ-12", "REQ-13"]
    assert first["tickets"] == ["BUG-142"]
    assert by_id["com.example.InvoiceTest::refund()"]["outcome"] == "error"
    assert by_id["com.example.InvoiceTest::later()"]["outcome"] == "skipped"


def test_jest_style_attachment_and_bad_requirement_reported():
    items, rejected = parse_junit(JEST_XML)
    assert items[0]["evidence"] == [{"kind": "screenshot", "uri": "reports/login.png"}]
    assert "requirements" not in items[0]
    assert rejected and "ignored" in rejected[0]["reason"]


def test_entity_expansion_and_oversize_are_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite>&a;</testsuite>'
    with pytest.raises(BadRequest):
        parse_junit(bomb)
    with pytest.raises(TooLarge):
        parse_junit(PYTEST_XML, max_bytes=10)
    with pytest.raises(BadRequest):
        parse_junit(b"<html/>")


def test_junit_upload_through_the_api(api):
    run_id = new_run(api, expected_count=3)
    resp = api(
        "POST",
        f"/projects/default/runs/{run_id}/junit",
        raw=JUNIT5_XML,
        content_type="application/xml",
    )
    assert resp.status == 200 and resp.payload["added"] == 3
    again = api(
        "POST",
        f"/projects/default/runs/{run_id}/junit",
        raw=JUNIT5_XML,
        content_type="application/xml",
    )
    assert again.payload["added"] == 0
    summary = finish(api, run_id).payload
    assert summary["tally"]["error"] == 1 and summary["tally"]["skipped"] == 1
    trace = api("GET", "/projects/default/traceability", query={"requirement": "REQ-13"})
    assert trace.payload["rows"][0]["tests"][0]["test_id"].endswith("totalsIncludeTax()")


def test_verdict_lines_round_trip_with_the_local_recorder(tmp_path):
    from verification.verdicts import CoverageOutcome, Kind, record_verdict

    path = tmp_path / "verdicts.jsonl"
    record_verdict(
        Kind.COVERAGE,
        CoverageOutcome.PROVED,
        subject="REQ-5",
        detail="ok",
        nodeid="tests/test_x.py::test_y",
        path=path,
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    rows, dropped = parse_verdict_lines([*lines, "{broken", json.dumps({"kind": "coverage"})])
    assert dropped == 2
    assert rows[0]["test_id"] == "tests/test_x.py::test_y"
    assert rows[0]["verdict"] == "proved" and rows[0]["subject"] == "REQ-5"
