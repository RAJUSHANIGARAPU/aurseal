"""Tests for contract_testing.schema (OpenAPIValidator)."""

from __future__ import annotations

import json
from pathlib import Path

from contract_testing.schema import OpenAPIValidator, _template_to_regex

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _write_spec(tmp_path, paths: dict) -> Path:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": paths,
    }
    p = tmp_path / "openapi.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def _simple_spec(tmp_path, required: list[str] | None = None) -> Path:
    req = required or ["id", "name"]
    return _write_spec(
        tmp_path,
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": req,
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    )


# ------------------------------------------------------------------
# _template_to_regex
# ------------------------------------------------------------------


def test_template_matches_numeric_segment():
    rx = _template_to_regex("/users/{id}")
    assert rx.match("/users/42")


def test_template_matches_string_segment():
    rx = _template_to_regex("/users/{id}")
    assert rx.match("/users/alice")


def test_template_does_not_match_extra_segments():
    rx = _template_to_regex("/users/{id}")
    assert not rx.match("/users/42/orders")


def test_template_multi_segment():
    rx = _template_to_regex("/users/{id}/orders/{order_id}")
    assert rx.match("/users/1/orders/99")
    assert not rx.match("/users/1/orders")


# ------------------------------------------------------------------
# OpenAPIValidator.validate — passing
# ------------------------------------------------------------------


def test_validate_passes_matching_body(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    errors = validator.validate("GET", "/users", 200, {"id": 1, "name": "Alice"})
    assert errors == []


def test_validate_passes_with_extra_fields_lenient(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path), strict=False)
    errors = validator.validate("GET", "/users", 200, {"id": 1, "name": "Alice", "extra": "x"})
    assert errors == []


def test_validate_passes_for_unknown_path(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    errors = validator.validate("GET", "/unknown", 200, {})
    assert errors == []


def test_validate_passes_for_unknown_status(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    errors = validator.validate("GET", "/users", 404, {})
    assert errors == []


# ------------------------------------------------------------------
# OpenAPIValidator.validate — failing
# ------------------------------------------------------------------


def test_validate_fails_missing_required_field(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    errors = validator.validate("GET", "/users", 200, {"id": 1})
    assert any("name" in e for e in errors)


def test_validate_fails_type_mismatch(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    errors = validator.validate("GET", "/users", 200, {"id": "not-int", "name": "Alice"})
    assert errors


def test_validate_fails_extra_fields_strict(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path), strict=True)
    errors = validator.validate("GET", "/users", 200, {"id": 1, "name": "Alice", "extra": "x"})
    assert errors


# ------------------------------------------------------------------
# Path template matching
# ------------------------------------------------------------------


def test_validates_path_with_template(tmp_path):
    spec = _write_spec(
        tmp_path,
        {
            "/users/{id}": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    )
    validator = OpenAPIValidator(spec)
    errors = validator.validate("GET", "/users/42", 200, {"id": 42})
    assert errors == []


# ------------------------------------------------------------------
# has_path
# ------------------------------------------------------------------


def test_has_path_exact(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    assert validator.has_path("GET", "/users")


def test_has_path_unknown(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    assert not validator.has_path("GET", "/nonexistent")


def test_has_path_wrong_method(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    assert not validator.has_path("DELETE", "/users")


# ------------------------------------------------------------------
# YAML spec loading
# ------------------------------------------------------------------


def test_loads_yaml_spec(tmp_path):
    yaml_spec = """
openapi: "3.0.0"
info:
  title: YAML Test
  version: "1.0.0"
paths:
  /items:
    get:
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                required: [sku]
                properties:
                  sku:
                    type: string
"""
    p = tmp_path / "spec.yaml"
    p.write_text(yaml_spec, encoding="utf-8")
    validator = OpenAPIValidator(p)
    errors = validator.validate("GET", "/items", 200, {"sku": "ABC-123"})
    assert errors == []


# ------------------------------------------------------------------
# Caching — same path matched twice returns same result
# ------------------------------------------------------------------


def test_path_cache_returns_consistent_result(tmp_path):
    validator = OpenAPIValidator(_simple_spec(tmp_path))
    e1 = validator.validate("GET", "/users", 200, {"id": 1, "name": "Alice"})
    e2 = validator.validate("GET", "/users", 200, {"id": 1, "name": "Alice"})
    assert e1 == e2 == []
