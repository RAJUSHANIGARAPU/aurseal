"""OpenAPI schema diff: each breaking class caught, additive changes pass."""

from __future__ import annotations

import copy

import pytest

from contract_testing.openapi_diff import BREAKING, NON_BREAKING, diff_openapi

BASE = {
    "openapi": "3.0.0",
    "paths": {
        "/orders": {
            "get": {
                "parameters": [{"name": "page", "in": "query", "schema": {"type": "integer"}}],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/List"}}
                        }
                    }
                },
            },
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["item"],
                                "properties": {
                                    "item": {"type": "string"},
                                    "note": {"type": "string"},
                                    "channel": {"type": "string", "enum": ["web", "phone"]},
                                },
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "created"}, "422": {"description": "bad"}},
            },
        },
        "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
    },
    "components": {
        "schemas": {
            "List": {
                "type": "object",
                "required": ["items", "total"],
                "properties": {
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Order"}},
                    "total": {"type": "integer"},
                },
            },
            "Order": {
                "type": "object",
                "required": ["id", "status"],
                "properties": {
                    "id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["open", "closed"]},
                },
            },
        }
    },
}


def _changed(mutate):
    new = copy.deepcopy(BASE)
    mutate(new)
    return diff_openapi(BASE, new)


def _rules(changes, kind=BREAKING):
    return {c.rule for c in changes if c.kind == kind}


def test_identical_specs_have_no_changes():
    assert diff_openapi(BASE, copy.deepcopy(BASE)) == []


@pytest.mark.parametrize(
    "mutate,rule",
    [
        (lambda s: s["paths"].pop("/health"), "path_removed"),
        (lambda s: s["paths"]["/orders"].pop("post"), "operation_removed"),
        (lambda s: s["paths"]["/orders"]["post"]["responses"].pop("201"), "status_removed"),
        (
            lambda s: s["components"]["schemas"]["Order"]["properties"].pop("status"),
            "property_removed",
        ),
        (
            lambda s: s["components"]["schemas"]["Order"]["properties"]["id"].update(type="string"),
            "type_changed",
        ),
        (
            lambda s: s["components"]["schemas"]["Order"]["required"].remove("status"),
            "became_optional",
        ),
        (
            lambda s: s["components"]["schemas"]["Order"]["properties"]["status"]["enum"].append(
                "archived"
            ),
            "enum_widened",
        ),
        (
            lambda s: s["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"][
                "schema"
            ]["properties"]["channel"]["enum"].remove("phone"),
            "enum_narrowed",
        ),
        (
            lambda s: s["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"][
                "schema"
            ]["required"].append("note"),
            "became_required",
        ),
        (
            lambda s: s["paths"]["/orders"]["get"]["parameters"].append(
                {"name": "tenant", "in": "header", "required": True, "schema": {"type": "string"}}
            ),
            "req_param",
        ),
        (
            lambda s: s["paths"]["/orders"]["get"]["parameters"][0].update(required=True),
            "param_required",
        ),
        (
            lambda s: s["paths"]["/orders"]["post"]["requestBody"].update(required=True),
            "body_required",
        ),
    ],
)
def test_each_breaking_class_is_caught(mutate, rule):
    changes = _changed(mutate)
    assert rule in _rules(changes), changes


def test_new_required_request_property_is_breaking():
    def mutate(s):
        schema = s["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]
        schema["properties"]["qty"] = {"type": "integer"}
        schema["required"].append("qty")

    assert "req_property" in _rules(_changed(mutate))


def test_additive_changes_are_not_breaking():
    def mutate(s):
        s["paths"]["/customers"] = {"get": {"responses": {"200": {"description": "ok"}}}}
        s["paths"]["/health"]["post"] = {"responses": {"204": {"description": "ok"}}}
        s["components"]["schemas"]["Order"]["properties"]["created"] = {"type": "string"}
        s["paths"]["/orders"]["get"]["parameters"].append(
            {"name": "sort", "in": "query", "schema": {"type": "string"}}
        )
        s["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"]["schema"][
            "properties"
        ]["tag"] = {"type": "string"}
        s["paths"]["/orders"]["post"]["responses"]["409"] = {"description": "conflict"}

    changes = _changed(mutate)
    assert _rules(changes) == set(), [str(c) for c in changes]
    assert {"path_added", "operation_added", "property_added", "opt_param", "status_added"} <= (
        _rules(changes, NON_BREAKING)
    )


def test_not_an_openapi_document_is_refused():
    with pytest.raises(ValueError):
        diff_openapi({"info": {}}, BASE)
