"""TestRail- and Xray-style payloads: golden output, unmapped cases reported, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from results.api import dispatch
from results.cli import main
from results.io.adapters.base import PushNotSupported, mapping_from_store
from results.io.adapters.testrail import TestRailAdapter
from results.io.adapters.xray import XrayAdapter
from results.io.export import result_rows
from results.io.mapping import load_mapping
from results.models import CaseRecord, EvidenceRecord, ResultRecord, RunRecord

GOLDEN = Path(__file__).parent / "golden"


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))


def _seed(store, tmp_path):
    store.upsert_cases(
        [
            CaseRecord(
                "c-a",
                "t.py::test_a",
                title="Pay",
                steps=("Open", "Pay"),
                links=(("requirement", "REQ-12"),),
                priority="P1",
                markers=({"name": "smoke", "args": []},),
            ),
            CaseRecord("c-b", "t.py::test_b", title="Refund"),
            CaseRecord("c-c", "t.py::test_c", title="Skip me"),
            CaseRecord("c-d", "t.py::test_d", title="Unmapped"),
        ]
    )
    mapping = tmp_path / "map.csv"
    mapping.write_text("case_id,tracker_id\nc-a,C101\nc-b,C102\nc-c,C103\n")
    assert load_mapping(mapping, "tms:testrail", store=store) == 3
    assert load_mapping(mapping, "tms:testrail", store=store) == 0
    xmap = tmp_path / "xmap.csv"
    xmap.write_text("case_key,tracker_id\nt.py::test_a,QA-1\nt.py::test_b,QA-2\n")
    store.add_links([("c-a", "tms:xray", "QA-1"), ("c-b", "tms:xray", "QA-2")], source="import")
    store.begin_run(
        RunRecord(
            run_id="r1",
            sut_version="2.1.0",
            environment="qa",
            started_at="2026-01-05T10:00:00.000Z",
        )
    )
    store.add_results(
        [
            ResultRecord(
                "r1",
                "t.py::test_a",
                "passed",
                case_id="c-a",
                duration_ms=65000,
                started_at="2026-01-05T10:00:01.000Z",
                finished_at="2026-01-05T10:01:06.000Z",
            ),
            ResultRecord(
                "r1",
                "t.py::test_b",
                "failed",
                case_id="c-b",
                message="refund was 0",
                defect_ref="BUG-142",
            ),
            ResultRecord("r1", "t.py::test_c", "skipped", case_id="c-c", skip_reason="no env"),
            ResultRecord("r1", "t.py::test_d", "passed", case_id="c-d"),
        ]
    )
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png")
    store.add_evidence([EvidenceRecord("r1", "screenshot", str(shot), nodeid="t.py::test_b")])
    store.finish_run("r1", "finished", finished_at="2026-01-05T10:05:00.000Z")


def test_testrail_golden_and_unmapped(store, tmp_path):
    _seed(store, tmp_path)
    adapter = TestRailAdapter()
    mapped = adapter.map_results(
        store.run("r1"), result_rows(store, "r1"), mapping_from_store(store, "tms:testrail")
    )
    assert mapped.unmapped == ["t.py::test_d"]  # reported, not dropped
    assert mapped.omitted == ["t.py::test_c"]  # skipped: TestRail cannot set untested
    payload = adapter.format_results(
        store.run("r1"), result_rows(store, "r1"), mapping_from_store(store, "tms:testrail")
    )
    assert payload.decode() == (GOLDEN / "testrail-results.json").read_text()
    from results.catalogue import catalogue_from_store

    cases_csv = adapter.format_cases(catalogue_from_store(store).cases)
    assert b"\r\n" in cases_csv  # CSV line ends; the golden file is stored with \n
    assert cases_csv.replace(b"\r\n", b"\n") == (GOLDEN / "testrail-cases.csv").read_bytes()


def test_xray_golden_and_junit_flavour(store, tmp_path):
    _seed(store, tmp_path)
    adapter = XrayAdapter(artifact_root=tmp_path)
    evidence = {}
    for ev in store.query("run_evidence", run_id="r1"):
        evidence.setdefault(ev["nodeid"], []).append(ev)
    payload = json.loads(
        adapter.format_results(
            store.run("r1"),
            result_rows(store, "r1"),
            mapping_from_store(store, "tms:xray"),
            evidence,
        )
    )
    assert adapter.last.unmapped == ["t.py::test_c", "t.py::test_d"]
    assert payload["tests"][1]["evidence"][0] == {
        "data": "cG5n",
        "filename": "shot.png",
        "contentType": "image/png",
    }
    payload["tests"][1].pop("evidence")
    assert payload == json.loads((GOLDEN / "xray-results.json").read_text())
    xml = adapter.format_junit(
        store.run("r1"), result_rows(store, "r1"), mapping_from_store(store, "tms:xray")
    )
    assert b'<property name="test_key" value="QA-1"' in xml and b"<failure" in xml


def test_dry_run_push_makes_no_network_call(store, tmp_path):
    _seed(store, tmp_path)
    transport = FakeTransport()
    adapter = TestRailAdapter(transport=transport)
    payload = adapter.format_results(
        store.run("r1"), result_rows(store, "r1"), mapping_from_store(store, "tms:testrail")
    )
    report = adapter.push(payload, dry_run=True)
    assert report.dry_run and not report.sent and report.unmapped == ("t.py::test_d",)
    assert transport.calls == []
    with pytest.raises(PushNotSupported):
        adapter.push(payload, dry_run=False)
    assert transport.calls == []


def test_mapping_rejects_unknown_cases_and_bad_kinds(store, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("case_id,tracker_id\nc-ghost,C9\n")
    with pytest.raises(ValueError, match="unknown case"):
        load_mapping(bad, "tms:testrail", store=store)
    with pytest.raises(ValueError, match="tms:"):
        load_mapping(bad, "testrail", store=store)


def test_rest_and_cli(store, tmp_path, capsys):
    _seed(store, tmp_path)
    tr = dispatch(store, "GET", "/exports/runs/r1/testrail")
    assert tr["unmapped"] == ["t.py::test_d"] and len(tr["payload"]["results"]) == 2
    xr = dispatch(store, "GET", "/exports/runs/r1/xray")
    assert xr["payload"]["info"]["testEnvironments"] == ["qa"]
    assert main(["export", "tms", "xray", "--run-id", "r1", "--push", "--dry-run"]) == 0
    err = capsys.readouterr().err
    assert "unmapped: t.py::test_d" in err and "dry run" in err
    assert main(["export", "tms", "testrail", "--run-id", "r1", "--push"]) == 2
    assert main(["export", "tms", "testrail", "--what", "cases"]) == 0
    assert main(["export", "tms", "xray", "--run-id", "r1", "--what", "junit"]) == 0
    m = tmp_path / "m.csv"
    m.write_text("case_id,tracker_id\nc-d,C104\n")
    assert main(["import", "mapping", str(m), "--kind", "tms:testrail"]) == 0
