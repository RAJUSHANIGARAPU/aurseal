"""Catalogue JSON validates against its schema; the Markdown matches a golden file."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from results.bundle import load_schema
from results.catalogue import Catalogue, write_catalogue
from results.models import CaseRecord

GOLDEN = Path(__file__).parent / "golden" / "catalogue.md"


def _catalogue() -> Catalogue:
    return Catalogue(
        cases=[
            CaseRecord(
                "c-1",
                "tests/test_billing.py::test_pay",
                title="Paying marks the invoice paid",
                file_path="tests/test_billing.py",
                owner="team-pay",
                priority="P1",
                steps=("Create an invoice", "Pay it"),
                links=(("feature", "billing"), ("requirement", "REQ-12")),
                markers=({"name": "requirement", "args": ["REQ-12"]},),
            ),
            CaseRecord(
                "TC-0042",
                "tests/test_billing.py::test_refund",
                title="Refund | partial",
                file_path="tests/test_billing.py",
            ),
            CaseRecord(
                "c-2",
                "tests/test_search.py::TestSearch.test_empty",
                title="Empty search",
                file_path="tests/test_search.py",
            ),
        ],
        generated_at="2026-01-01T00:00:00.000Z",
    )


def test_json_validates_against_the_schema(tmp_path):
    json_path, md_path = write_catalogue(_catalogue(), tmp_path)
    data = json.loads(json_path.read_text())
    Draft202012Validator(load_schema("catalogue-v1.schema.json")).validate(data)
    assert data["case_count"] == 3 and data["complete"] is True
    assert md_path.read_text() == _catalogue().to_markdown()


def test_markdown_matches_the_golden_file():
    assert _catalogue().to_markdown() == GOLDEN.read_text()


def test_json_round_trips():
    cat = _catalogue()
    again = Catalogue.from_json(json.loads(json.dumps(cat.to_json())))
    assert {c.case_id: c.links for c in again.cases} == {c.case_id: c.links for c in cat.cases}
