"""CSV header order, quoting and unicode; JSON validates against its schema."""

from __future__ import annotations

import csv
import io
import json

import pytest
from jsonschema import Draft202012Validator

from results.api import dispatch
from results.bundle import load_schema
from results.cli import main
from results.io.export import CASE_COLUMNS, RESULT_COLUMNS, export_cases, export_results
from results.models import CaseRecord, ResultRecord, RunRecord
from results.store import StoreError


def _seed(store):
    store.upsert_cases(
        [
            CaseRecord(
                "c-1",
                "tests/test_x.py::test_a",
                title='Ünïcode, "quotes", and\nnewline',
                owner="team-a",
                markers=({"name": "smoke", "args": []},),
                links=(("requirement", "REQ-12"),),
            )
        ]
    )
    store.begin_run(RunRecord(run_id="r1"))
    store.add_results(
        [
            ResultRecord(
                "r1", "tests/test_x.py::test_a", "failed", 1, case_id="c-1", message='boom, "x"'
            ),
            ResultRecord(
                "r1", "tests/test_x.py::test_a", "passed", 2, case_id="c-1", duration_ms=5
            ),
        ]
    )


def test_cases_csv(store):
    _seed(store)
    data = export_cases(store, "csv")
    assert data.decode("utf-8").splitlines()[0] == ",".join(CASE_COLUMNS)
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    assert rows[0]["title"] == 'Ünïcode, "quotes", and\nnewline'
    assert rows[0]["links"] == "requirement:REQ-12" and rows[0]["markers"] == "smoke"
    assert b"\r\n" in data  # RFC 4180 line ends


def test_results_csv_is_the_final_attempt(store):
    _seed(store)
    rows = list(csv.DictReader(io.StringIO(export_results(store, "r1", "csv").decode("utf-8"))))
    assert list(rows[0]) == list(RESULT_COLUMNS)
    assert rows[0]["outcome"] == "passed" and rows[0]["attempts"] == "2"


def test_json_exports_validate(store):
    _seed(store)
    cases = json.loads(export_cases(store, "json"))
    Draft202012Validator(load_schema("cases-export-v1.schema.json")).validate(cases)
    results = json.loads(export_results(store, "r1", "json"))
    Draft202012Validator(load_schema("results-export-v1.schema.json")).validate(results)
    assert results["run"]["run_id"] == "r1" and results["result_count"] == 1


def test_unknown_run(store):
    with pytest.raises(StoreError):
        export_results(store, "nope", "csv")


def test_rest_and_cli(store, tmp_path, capsys):
    _seed(store)
    assert dispatch(store, "GET", "/exports/cases.csv")["content_type"] == "text/csv"
    body = dispatch(store, "GET", "/exports/runs/r1/results.json")["body"]
    assert json.loads(body)["result_count"] == 1
    assert main(["export", "cases", "--format", "json", "--out", str(tmp_path / "c.json")]) == 0
    assert json.loads((tmp_path / "c.json").read_text())["case_count"] == 1
    assert main(["export", "results", "--run-id", "r1"]) == 0
    assert "tests/test_x.py::test_a" in capsys.readouterr().out
    assert main(["export", "results", "--run-id", "nope"]) == 2


def test_import_report_schema():
    Draft202012Validator.check_schema(load_schema("junit-import-report-v1.schema.json"))
