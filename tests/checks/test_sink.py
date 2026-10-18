"""The metric sink seam: JSONL default, store adapter, stamping, failures reported."""

from __future__ import annotations

import pytest

import checks._sink as sink_module
from checks._sink import JsonlSink, StoreSink, default_sink, stamp, write_metrics


@pytest.fixture(autouse=True)
def example_schema(tmp_path, monkeypatch):
    """A permissive schema for the made-up ``metric_example`` table these tests write."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "metric_example.schema.json").write_text('{"type": "object"}', encoding="utf-8")
    monkeypatch.setattr(sink_module, "SCHEMA_DIR", schemas)


def test_stamp_fills_common_columns_without_overriding_caller_values():
    rows = stamp([{"x": 1}, {"x": 2, "nodeid": "t.py::a"}], env={"TEST_RUN_ID": "r-1"})
    assert {r["run_id"] for r in rows} == {"r-1"}
    assert rows[0]["nodeid"] is None and rows[1]["nodeid"] == "t.py::a"
    assert rows[0]["id"] != rows[1]["id"]
    assert stamp([{}], env={})[0]["run_id"].startswith("local-")


def test_jsonl_sink_appends_and_reads_back(tmp_path):
    sink = JsonlSink(tmp_path / "metrics")
    sink.write("metric_example", [{"a": 1}])
    sink.write("metric_example", [{"a": 2}])
    assert [r["a"] for r in sink.read("metric_example")] == [1, 2]


def test_table_name_is_validated(tmp_path):
    with pytest.raises(ValueError):
        JsonlSink(tmp_path / "metrics").write("results; drop", [{"a": 1}])


def test_store_sink_needs_write_metrics():
    with pytest.raises(TypeError):
        StoreSink(object())

    got = []

    class Store:
        def write_metrics(self, table, rows):
            got.append((table, rows))

    StoreSink(Store()).write("metric_example", [{"a": 1}])
    assert got == [("metric_example", [{"a": 1}])]


def test_default_sink_choices(tmp_path):
    assert default_sink({"METRICS_SINK": "off"}) is None
    sink = default_sink({"METRICS_DIR": str(tmp_path)})
    assert isinstance(sink, JsonlSink) and sink.root == tmp_path


def test_write_failure_is_reported_not_raised():
    class Broken:
        def write(self, table, rows):
            raise OSError("disk full")

    problem = write_metrics("metric_example", [{"a": 1}], sink=Broken())
    assert problem and "disk full" in problem
    assert write_metrics("metric_example", [{"a": 1}], env={"METRICS_SINK": "off"}) is None
