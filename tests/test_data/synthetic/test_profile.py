"""Profiles from JSON and from OpenAPI; the checker names every broken constraint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_data.synthetic import Profile

ROOT = Path(__file__).resolve().parents[3]

SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/api/items": {
            "post": {
                "operationId": "item_create",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Item"}}
                    }
                },
            }
        }
    },
    "components": {
        "schemas": {
            "Item": {
                "type": "object",
                "required": ["sku"],
                "properties": {
                    "sku": {"type": "string", "pattern": "^SKU-[0-9]{4}$", "maxLength": 8},
                    "qty": {"type": "integer", "minimum": 1},
                    "kind": {"type": "string", "enum": ["a", "b"]},
                    "contact": {"type": "string", "format": "email"},
                    "tags": {"type": "array"},
                },
            }
        }
    },
}


def test_from_openapi_reads_basic_constraints():
    profile = Profile.from_openapi(SPEC, "item_create")
    assert profile.endpoint == "/api/items"
    sku = profile.get_field("sku")
    assert sku.required and sku.pattern == "^SKU-[0-9]{4}$" and sku.max_len == 8
    assert profile.get_field("qty").min == 1
    assert profile.get_field("kind").choices == ("a", "b")
    assert profile.get_field("contact").kind == "email"
    with pytest.raises(KeyError):
        profile.get_field("tags")  # arrays are not profiled


def test_from_openapi_refuses_combinators_and_unknown_operations():
    spec = json.loads(json.dumps(SPEC))
    spec["components"]["schemas"]["Item"]["properties"]["qty"] = {"oneOf": [{"type": "integer"}]}
    with pytest.raises(ValueError, match="combinators"):
        Profile.from_openapi(spec, "item_create")
    with pytest.raises(KeyError):
        Profile.from_openapi(SPEC, "nope")


def test_demo_openapi_spec_profiles():
    spec = json.loads((ROOT / "demo_sut" / "openapi.json").read_text(encoding="utf-8"))
    profile = Profile.from_openapi(spec, "customer_create")
    assert profile.get_field("type").choices == ("INDIVIDUAL", "COMPANY")
    assert profile.get_field("display_name").required


def test_check_names_every_violation():
    profile = Profile.from_openapi(SPEC, "item_create")
    assert profile.check({"sku": "SKU-1234"}) == []
    assert profile.check({"qty": 0, "kind": "c", "contact": "x"}) == [
        "sku.required",
        "qty.min",
        "kind.choices",
        "contact.format",
    ]
    assert profile.check({"sku": 5}) == ["sku.type"]


def test_json_round_trip(tmp_path):
    profile = Profile.from_openapi(SPEC, "item_create")
    path = tmp_path / "p.json"
    path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
    assert Profile.from_json(path) == profile


def test_profile_schema_accepts_the_repository_profile():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT / "schemas" / "synthetic-profile.schema.json").read_text())
    for path in (ROOT / "test_data" / "profiles").glob("*.json"):
        jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"entity": "x", "fields": [{"name": "a", "kind": "blob"}]}, schema)
