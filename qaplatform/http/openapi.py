"""OpenAPI 3.1 generated from :data:`~qaplatform.http.routes.ROUTES` and the JSON Schemas.

Generated, never hand-written: the route table and the schema files are the contract, and
this document is a view of them that client generators understand.
"""

from __future__ import annotations

import copy
from typing import Any

from qaplatform import schemas
from qaplatform.http.routes import PREFIX, PROBLEM_TYPE, ROUTES

_ERRORS = {
    "400": "Request does not match its schema",
    "401": "Missing, expired or revoked token",
    "403": "Token lacks the permission",
    "404": "Unknown or inaccessible project or resource",
    "409": "Run finished, or Idempotency-Key reused",
    "413": "Body over the limit",
}


def _component_name(file_name: str) -> str:
    return file_name.removesuffix(".schema.json")


def _rewrite_refs(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in ("$id", "$schema"):
                continue
            if key == "$ref" and isinstance(value, str) and value.endswith(".schema.json"):
                out[key] = f"#/components/schemas/{_component_name(value.split('/')[-1])}"
            else:
                out[key] = _rewrite_refs(value)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(v) for v in node]
    return node


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _path_params(path: str) -> list[str]:
    return [p[1:-1] for p in path.split("/") if p.startswith("{") and p.endswith("}")]


def build_openapi() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for route in ROUTES:
        op: dict[str, Any] = {"operationId": route.name, "summary": route.summary}
        params = [
            {"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
            for n in _path_params(route.path)
        ]
        params += [
            {"name": n, "in": "query", "required": False, "schema": {"type": "string"}}
            for n in route.query
        ]
        if route.idempotent_post:
            params.append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string", "maxLength": 200},
                }
            )
        if params:
            op["parameters"] = params
        if route.body == "junit":
            op["requestBody"] = {
                "required": True,
                "content": {"application/xml": {"schema": {"type": "string"}}},
            }
        elif route.body is not None:
            content: dict[str, Any] = {
                "application/json": {"schema": _ref(route.request_schema)}
                if route.request_schema
                else {"schema": {"type": "object"}}
            }
            if route.body == "verdicts":
                content["application/x-ndjson"] = {"schema": {"type": "string"}}
            op["requestBody"] = {"required": True, "content": content}
        ok_schema = _ref(route.response_schema) if route.response_schema else {"type": "object"}
        responses: dict[str, Any] = {
            str(route.status): {
                "description": "OK",
                "content": {"application/json": {"schema": ok_schema}},
            }
        }
        if route.name == "create_run":
            responses["200"] = {
                "description": "A run with this external_id already exists",
                "content": {"application/json": {"schema": ok_schema}},
            }
        problem = {PROBLEM_TYPE: {"schema": _ref("problem")}}
        for code, text in _ERRORS.items():
            if code == "401" and not route.auth:
                continue
            responses[code] = {"description": text, "content": problem}
        op["responses"] = responses
        if route.auth:
            op["security"] = [{"bearer": []}]
        paths.setdefault(PREFIX + route.path, {})[route.method.lower()] = op
    components = {n: _rewrite_refs(copy.deepcopy(schemas.load(n))) for n in schemas.names()}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Test results platform API",
            "version": schemas.SCHEMA_VERSION,
            "description": "Push test results from any framework; read verdicts, "
            "traceability and run trust; trigger runs.",
        },
        "paths": paths,
        "components": {
            "schemas": components,
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        },
    }


__all__ = ["build_openapi"]
