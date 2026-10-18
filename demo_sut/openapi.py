"""Build the OpenAPI document from the route table.

The committed ``demo_sut/openapi.json`` must equal :func:`build`; a test fails when they
drift. Regenerate with ``python -m demo_sut openapi --write``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from demo_sut.app import router

SPEC_PATH = Path(__file__).parent / "openapi.json"
VERSION = "1.0.0"


def build() -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    for route in router.routes:
        if not route.api:
            continue
        params = [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
            for name in re.findall(r"\{(\w+)\}", route.path)
        ]
        params += [
            {"name": name, "in": "query", "required": False, "schema": {"type": "string"}}
            for name in route.query
        ]
        params += [
            {"name": name, "in": "header", "required": False, "schema": {"type": "string"}}
            for name in route.headers
        ]
        operation: dict[str, Any] = {
            "operationId": route.handler.__name__,
            "summary": route.summary,
            "tags": [route.tag],
            "responses": {
                str(route.status): {
                    "description": "Success",
                    "content": {route.produces: {"schema": {}}},
                },
                "default": {
                    "description": "Error body {error, message, ...}",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                },
            },
        }
        if params:
            operation["parameters"] = params
        if route.body is not None:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": route.body}},
            }
        if route.path == "/oauth/token":
            operation["requestBody"] = {
                "required": True,
                "content": {"application/x-www-form-urlencoded": {"schema": {"type": "object"}}},
            }
        paths.setdefault(route.path, {})[route.method.lower()] = operation
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Demo subscription service",
            "version": VERSION,
            "description": "Invented subscription and billing service used as a local "
            "system under test. Every response carries X-Demo-Faults and X-Demo-Clock.",
        },
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": {
                "Error": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {"error": {"type": "string"}, "message": {"type": "string"}},
                }
            }
        },
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=False) + "\n"


def write(path: Path = SPEC_PATH) -> Path:
    path.write_text(render())
    return path
