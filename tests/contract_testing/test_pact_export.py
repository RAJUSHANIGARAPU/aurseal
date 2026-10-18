"""Pact v3 export: valid shape, example bodies, type matching rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.pact_export import (
    example_from_schema,
    matching_rules,
    to_pact_v3,
    write_pact,
)

SCHEMA = json.loads(
    (Path(__file__).parent / "assets" / "pact_v3_subset.schema.json").read_text(encoding="utf-8")
)
BODY = {
    "type": "object",
    "required": ["id", "tags", "owner"],
    "properties": {
        "id": {"type": "integer"},
        "price": {"type": "number"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "owner": {"type": "object", "properties": {"email-address": {"type": "string"}}},
        "state": {"type": "string", "enum": ["open", "closed"]},
    },
}


def _contract():
    return Contract(
        consumer="web",
        provider="api",
        version="2.1.0",
        interactions=[
            Interaction(
                description="list orders",
                request=RequestSchema(method="get", path="/orders", query="page=1&page=2"),
                response=ResponseSchema(status=200, body_schema=BODY),
                provider_states=["orders exist"],
            ),
            Interaction(
                description="",
                request=RequestSchema(
                    method="POST",
                    path="/orders",
                    body_schema={"type": "object", "properties": {"item": {"type": "string"}}},
                ),
                response=ResponseSchema(status=201),
            ),
        ],
    )


def test_output_validates_against_the_pact_v3_subset():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(to_pact_v3(_contract()), SCHEMA)


def test_types_round_trip_into_examples_and_matching_rules():
    pact = to_pact_v3(_contract())
    response = pact["interactions"][0]["response"]
    assert response["body"] == {
        "id": 1,
        "price": 1.5,
        "tags": ["string"],
        "owner": {"email-address": "string"},
        "state": "open",
    }
    rules = response["matchingRules"]["body"]
    assert rules["$.id"] == {"matchers": [{"match": "type"}]}
    assert rules["$.tags"] == {"matchers": [{"match": "type", "min": 0}]}
    assert "$.tags[*]" in rules
    assert "$.owner['email-address']" in rules
    assert pact["interactions"][0]["providerStates"] == [{"name": "orders exist"}]
    assert pact["interactions"][0]["request"]["query"] == {"page": ["1", "2"]}
    assert pact["interactions"][1]["request"]["body"] == {"item": "string"}
    assert pact["interactions"][1]["description"] == "POST /orders"
    assert pact["metadata"]["pactSpecification"]["version"] == "3.0.0"


def test_example_and_rules_of_empty_schema():
    assert example_from_schema({}) is None
    assert matching_rules({}) == {}


def test_write_pact(tmp_path):
    path = write_pact(_contract(), tmp_path / "pacts")
    assert path.name == "web-api.json"
    assert json.loads(path.read_text())["consumer"] == {"name": "web"}
