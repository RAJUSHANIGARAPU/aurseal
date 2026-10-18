"""Each gap source parses its input; an empty or unreadable source is an error."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.traceability._runs import registry
from traceability.gaps import (
    GapSourceError,
    OpenApiSource,
    PostmanSource,
    RequirementSource,
    SpecSource,
)

ROOT = Path(__file__).resolve().parents[2]

OPENAPI = {
    "openapi": "3.0.0",
    "paths": {
        "/api/orders": {"get": {"summary": "list"}, "post": {"operationId": "create"}},
        "/api/orders/{id}": {"get": {}, "parameters": []},
    },
}
SPEC = """---
feature: orders
---

# Orders

## Scenario Create an order

### Steps
1. Post an order

### Tags
`smoke` `REQ-1` BUG-3

## Scenario: Cancel an order

### Tags
`regression`

## Notes
not a scenario
"""


def test_openapi_operations(tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps(OPENAPI))
    items = OpenApiSource(path).items()
    assert [(i.key, i.title) for i in items] == [
        ("GET /api/orders", "list"),
        ("POST /api/orders", "create"),
        ("GET /api/orders/{id}", ""),
    ]
    assert items[0].ref == "openapi:GET /api/orders"


def test_the_shipped_openapi_document_parses():
    assert len(OpenApiSource(ROOT / "demo_sut" / "openapi.json").items()) > 10


def test_spec_scenarios_with_requirement_tags(tmp_path):
    (tmp_path / "orders.md").write_text(SPEC)
    (tmp_path / "README.md").write_text("## Scenario ignored\n")
    items = SpecSource(tmp_path).items()
    assert [(i.key, i.requirements) for i in items] == [
        ("orders#create-an-order", ("REQ-1",)),
        ("orders#cancel-an-order", ()),
    ]


def test_postman_requests(tmp_path):
    coll = {
        "info": {},
        "item": [
            {
                "name": "folder",
                "item": [
                    {
                        "name": "one",
                        "request": {"method": "get", "url": {"path": ["orders", ":id"]}},
                    },
                    {
                        "name": "two",
                        "request": {"method": "POST", "url": "https://x.example.test/orders?a=1"},
                    },
                    {
                        "name": "dup",
                        "request": {"method": "GET", "url": {"path": ["orders", "{{id}}"]}},
                    },
                ],
            }
        ],
    }
    path = tmp_path / "c.json"
    path.write_text(json.dumps(coll))
    assert [i.key for i in PostmanSource(path).items()] == ["GET /orders/{}", "POST /orders"]


def test_requirement_items():
    items = RequirementSource(registry("REQ-1", "REQ-2")).items()
    assert [(i.key, i.requirements) for i in items] == [
        ("REQ-1", ("REQ-1",)),
        ("REQ-2", ("REQ-2",)),
    ]


@pytest.mark.parametrize(
    "make",
    [
        lambda p: OpenApiSource(p / "missing.json"),
        lambda p: OpenApiSource(_write(p / "e.json", {"paths": {}})),
        lambda p: SpecSource(p),
        lambda p: PostmanSource(_write(p / "c.json", {"item": []})),
        lambda p: RequirementSource(registry()),
    ],
)
def test_an_empty_source_is_an_error_not_zero_gaps(tmp_path, make):
    with pytest.raises(GapSourceError):
        make(tmp_path).items()


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data))
    return path
