"""Tests for contract_testing.consumed_fields: consumer reads vs the published API document."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from contract_testing.consumed_fields import (
    load_declaration,
    main,
    missing,
    path_matches,
    schema_properties,
)

DOC = {
    "openapi": "3.0.3",
    "info": {"title": "Demo", "version": "1"},
    "paths": {
        "/api/subscriptions/{subscriptionId}": {"get": {}, "patch": {}},
        "/api/subscriptions": {"get": {}, "post": {}, "parameters": []},
    },
    "components": {
        "schemas": {
            "Base": {"properties": {"id": {}, "version": {}}},
            "Subscription": {
                "allOf": [{"$ref": "#/components/schemas/Base"}],
                "properties": {"number": {}, "status": {}},
            },
            "Invoice": {"$ref": "#/components/schemas/Base"},
        }
    },
}


def test_everything_consumed_is_present():
    consumed = {"Subscription": ["id", "number", "status"], "Invoice": ["version"]}
    routes = [("/subscriptions/{id}", "GET"), ("/api/subscriptions", "post")]
    assert missing(DOC, consumed, routes) == []


def test_missing_fields_schemas_and_routes_are_named():
    consumed = {"Subscription": ["id", "price"], "Plan": ["code"]}
    routes = [("/subscriptions/{id}", "DELETE"), ("/plans", "GET")]
    assert missing(DOC, consumed, routes) == [
        "Subscription.price",
        "Plan.code",
        "DELETE /subscriptions/{id}",
        "GET /plans",
    ]


@pytest.mark.parametrize(
    ("consumer", "served", "expected"),
    [
        ("/subscriptions/{id}", "/api/subscriptions/{subscriptionId}", True),
        ("/service/v1/subscriptions", "/subscriptions", True),
        ("/subscriptions/{id}/versions", "/api/subscriptions/{id}", False),
        ("/subscription", "/api/subscriptions", False),
        ("/", "/", True),
        ("/", "/api", False),
    ],
)
def test_path_matching_tolerates_a_prefix(consumer, served, expected):
    assert path_matches(consumer, served) is expected


def test_schema_properties_follow_refs_and_all_of():
    assert schema_properties(DOC, DOC["components"]["schemas"]["Subscription"]) == {
        "id",
        "version",
        "number",
        "status",
    }


def _files(tmp_path: Path, declaration: str) -> tuple[str, str]:
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps(DOC))
    decl = tmp_path / "consumed.yaml"
    decl.write_text(declaration)
    return str(doc), str(decl)


def test_cli_passes_and_fails(tmp_path):
    ok = "schemas:\n  Subscription: [id, status]\nroutes:\n  - GET /subscriptions/{id}\n"
    out = io.StringIO()
    assert main(list(_files(tmp_path, ok)), out=out) == 0
    assert "all 3 consumed items present" in out.getvalue()

    bad = 'schemas:\n  Subscription: [price]\nroutes:\n  - [DELETE, "/subscriptions/{id}"]\n'
    out = io.StringIO()
    assert main(list(_files(tmp_path, bad)), out=out) == 1
    assert "Subscription.price" in out.getvalue() and "DELETE /subscriptions/{id}" in out.getvalue()


@pytest.mark.parametrize(
    "declaration",
    ["{}\n", "routes:\n  - FETCH /x\n", "routes:\n  - GET x\n", "- just a list\n"],
)
def test_declarations_that_would_check_nothing_or_are_malformed_are_refused(tmp_path, declaration):
    with pytest.raises(ValueError):
        load_declaration(_files(tmp_path, declaration)[1])
