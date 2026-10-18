"""k6 importer: thresholds become records, metrics a typed perf.k6 payload."""

from __future__ import annotations

import io
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from testkeel.importers import ReportError, registry
from testkeel.importers.k6 import SCHEMA_REF, K6Payload, K6ResultType
from testkeel.model.v1 import RunUpload, Status
from testkeel.plugin_api.v1 import RESULT_TYPES, ResultType, discover

FIXTURES = Path(__file__).parent / "fixtures"
HANDLE_SUMMARY = FIXTURES / "k6-summary.json"
SUMMARY_EXPORT = FIXTURES / "k6-summary-export.json"


def _records(path):
    return {(r.suite, r.name): r for r in registry.parse_file(path)}


@pytest.mark.parametrize("fixture", [HANDLE_SUMMARY, SUMMARY_EXPORT], ids=lambda p: p.name)
def test_both_inputs_are_sniffed_and_reimports_keep_identities(fixture):
    assert registry.detect(fixture.read_bytes()[:4096]).name == "k6"
    first = [r.identity for r in registry.parse_file(fixture)]
    assert len(first) == len(set(first))
    for _ in range(19):
        assert [r.identity for r in registry.parse_file(fixture)] == first


def test_handle_summary_thresholds_are_records():
    records = _records(HANDLE_SUMMARY)
    assert set(records) == {
        ("http_req_duration", "p(95)<500"),
        ("http_req_duration", "p(99)<1500"),
        ("http_req_failed", "rate<0.01"),
        ("checks", "rate>0.99"),
    }
    p95 = records[("http_req_duration", "p(95)<500")]
    assert (p95.framework, p95.path, p95.status) == ("k6", "", Status.FAILED)
    assert p95.message == "threshold p(95)<500 on http_req_duration crossed (observed 612.3)"
    assert records[("http_req_duration", "p(99)<1500")].status is Status.PASSED
    assert records[("checks", "rate>0.99")].status is Status.FAILED


def test_the_payload_is_typed_and_names_its_schema():
    record = _records(HANDLE_SUMMARY)[("http_req_duration", "p(95)<500")]
    assert record.result_type is not None and record.result_type.type == "perf.k6"
    payload = K6Payload.model_validate(record.result_type.payload)
    assert payload.schema_ref == SCHEMA_REF == "perf.k6/1"
    assert (payload.source, payload.metric_type, payload.contains) == (
        "handle_summary",
        "trend",
        "time",
    )
    assert payload.values["p(99)"] == 980.7 and payload.observed == 612.3
    assert (payload.ok, payload.test_run_duration_ms) == (False, 60123.5)


def test_summary_export_booleans_mean_failed():
    records = _records(SUMMARY_EXPORT)
    assert records[("http_req_duration", "p(95)<500")].status is Status.FAILED
    assert records[("http_req_duration", "p(90)<800")].status is Status.PASSED
    rate = records[("http_req_failed", "rate<0.01")]
    assert rate.status is Status.PASSED
    payload = K6Payload.model_validate(rate.result_type.payload)
    assert (payload.source, payload.metric_type) == ("summary_export", "rate")
    assert payload.values["rate"] == 0.004 and payload.observed == 0.004


def test_both_inputs_give_a_threshold_the_same_identity():
    shared = ("http_req_duration", "p(95)<500")
    assert (
        _records(HANDLE_SUMMARY)[shared].fingerprint == _records(SUMMARY_EXPORT)[shared].fingerprint
    )


def test_records_round_trip_through_the_upload_document():
    records = tuple(registry.parse_file(HANDLE_SUMMARY))
    upload = RunUpload(records=records)
    assert RunUpload.model_validate_json(upload.model_dump_json()) == upload


def test_the_result_type_is_a_registered_plugin():
    found = discover(RESULT_TYPES)
    assert isinstance(found["perf.k6"], ResultType)
    schema = found["perf.k6"].payload_schema()
    assert schema["$id"].endswith("perf.k6/1.json")
    assert {"metric", "threshold", "ok", "values"} <= set(schema["required"]) | set(
        schema["properties"]
    )
    assert "k6" in {ep.name for ep in entry_points(group="testkeel.importers")}
    with pytest.raises(ValueError):
        K6ResultType().validate({"metric": "x"})


def test_no_thresholds_means_no_records():
    body = b'{"root_group": {}, "metrics": {"vus": {"type": "gauge", "values": {"value": 1}}}}'
    assert list(registry.parse(io.BytesIO(body), format="k6")) == []


def test_the_machine_readable_summary_is_refused_with_a_reason():
    body = b'{"version": "1.0.0", "metadata": {"k6Version": "1.5.0"}, "results": {}}'
    assert registry.detect(body).name == "k6"
    with pytest.raises(ReportError, match="no threshold outcomes"):
        list(registry.parse(io.BytesIO(body)))


def test_malformed_input_is_a_report_error():
    with pytest.raises(ReportError):
        list(registry.parse(io.BytesIO(b'{"metrics": '), format="k6"))
    with pytest.raises(ReportError, match="no 'metrics'"):
        list(registry.parse(io.BytesIO(b'{"root_group": {}}'), format="k6"))
