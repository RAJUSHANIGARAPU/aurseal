"""Tests for oracles.schema_required: required inputs read from a served schema."""

from __future__ import annotations

import json

import pytest

from api.clients.http_engine import ApiResponse
from oracles.data_health import GetOnlyApi
from oracles.schema_required import (
    NotExpanded,
    SchemaNotFound,
    find_nodes,
    missing_required,
    served_missing_required,
)

CONFIG = {
    "machine": "subscription",
    "actions": [
        {
            "name": "cancel",
            "input": {
                "cancelInput": {
                    "type": "object",
                    "properties": {"reason": {}, "effective_date": {}, "note": {}},
                    "required": ["reason"],
                }
            },
        },
        {"name": "renew", "input": {"renewInput": {"$ref": "#/defs/renew"}}},
    ],
}


def test_names_required_inputs_the_schema_does_not_require():
    found = missing_required(CONFIG, "cancelInput", ["reason", "effective_date"])
    assert found == ["effective_date"]


def test_all_required_is_empty():
    assert missing_required(CONFIG, "cancelInput", ["reason"]) == []


def test_an_unexpanded_node_is_not_read_as_not_required():
    with pytest.raises(NotExpanded, match="not expanded"):
        missing_required(CONFIG, "renewInput", ["index_percent"])


def test_locate_by_callable_and_errors():
    node = CONFIG["actions"][0]["input"]["cancelInput"]
    assert missing_required(CONFIG, lambda doc: node, ["note"]) == ["note"]
    with pytest.raises(SchemaNotFound, match="0 nodes"):
        missing_required(CONFIG, "noSuchInput", ["x"])
    with pytest.raises(SchemaNotFound):
        missing_required(CONFIG, lambda doc: None, ["x"])
    with pytest.raises(SchemaNotFound, match="2 nodes"):
        missing_required({"a": {"s": {}}, "b": [{"s": {}}]}, "s", ["x"])
    with pytest.raises(ValueError):
        missing_required(CONFIG, "cancelInput", [])


def test_find_nodes_walks_lists_and_mappings():
    assert len(find_nodes({"x": [{"y": {"s": {"p": 1}}}]}, "s")) == 1


def test_served_schema_through_a_get_only_api():
    paths = []

    def get(path, params=None):
        paths.append(path)
        return ApiResponse(200, json.dumps(CONFIG).encode())

    api = GetOnlyApi(get)
    found = served_missing_required(
        api, "/api/machines/subscription", "cancelInput", ["reason", "note"]
    )
    assert found == ["note"]
    assert paths == ["/api/machines/subscription"]
    failing = GetOnlyApi(lambda path, params=None: ApiResponse(503))
    with pytest.raises(AssertionError, match="2xx"):
        served_missing_required(failing, "/x", "cancelInput", ["reason"])
