"""OpenAPI schema validator.

Loads an OpenAPI 3.x spec (JSON or YAML) and validates HTTP responses
against the schema defined for each path/method/status code.

Path matching handles OpenAPI path templates: /users/{id} matches /users/123.
$ref resolution is done inline using jsonschema's RefResolver.

Zero new dependencies: uses jsonschema (already in requirements) and
PyYAML (already in requirements).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Matches OpenAPI path templates like /users/{id}/orders/{order_id}
_TEMPLATE_RE = re.compile(r"\{[^}]+\}")


def _template_to_regex(template: str) -> re.Pattern:
    """Convert /users/{id}/orders to a regex that matches /users/123/orders."""
    parts = _TEMPLATE_RE.split(template)
    pattern = "[^/]+".join(re.escape(p) for p in parts)
    return re.compile(f"^{pattern}$")


class OpenAPIValidator:
    """Validates HTTP responses against an OpenAPI 3.x specification."""

    def __init__(self, spec_path: Path, strict: bool = False) -> None:
        self._spec = self._load(spec_path)
        self._spec_path = spec_path
        self._strict = strict
        self._path_cache: dict[str, str | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        method: str,
        path: str,
        status: int,
        response_body: Any,
    ) -> list[str]:
        """Validate a response body. Returns a list of error messages (empty = pass)."""
        schema = self._find_schema(method.lower(), path, status)
        if schema is None:
            return []
        return self._validate_body(response_body, schema)

    def has_path(self, method: str, path: str) -> bool:
        """Return True if the spec defines this method + path."""
        return self._match_path(path) is not None and method.lower() in self._spec.get(
            "paths", {}
        ).get(self._match_path(path) or "", {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml

                return yaml.safe_load(text) or {}
            except ImportError:
                logger.warning("PyYAML not available — falling back to JSON parse")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("[contract] Could not parse OpenAPI spec at %s", path)
            return {}

    def _find_schema(self, method: str, path: str, status: int) -> dict | None:
        matched_template = self._match_path(path)
        if matched_template is None:
            return None

        paths = self._spec.get("paths", {})
        path_obj = paths.get(matched_template, {})
        method_obj = path_obj.get(method, {})
        responses = method_obj.get("responses", {})

        # Try exact status, then wildcard (e.g., "2XX"), then "default"
        status_key = str(status)
        response_obj = (
            responses.get(status_key)
            or responses.get(status_key[0] + "XX")
            or responses.get("default")
        )
        if not response_obj:
            return None

        content = response_obj.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})
        if not schema:
            return None

        return self._resolve_refs(schema)

    def _match_path(self, request_path: str) -> str | None:
        """Find the OpenAPI path template that matches the given concrete path."""
        if request_path in self._path_cache:
            return self._path_cache[request_path]

        paths = self._spec.get("paths", {})
        # Exact match first
        if request_path in paths:
            self._path_cache[request_path] = request_path
            return request_path

        # Template matching
        for template in paths:
            if _TEMPLATE_RE.search(template):
                if _template_to_regex(template).match(request_path):
                    self._path_cache[request_path] = template
                    return template

        self._path_cache[request_path] = None
        return None

    def _resolve_refs(self, schema: dict) -> dict:
        """Inline-resolve $ref references within the spec."""
        if "$ref" not in schema:
            return schema
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return schema
        parts = ref.lstrip("#/").split("/")
        obj = self._spec
        for part in parts:
            obj = obj.get(part, {})
        return self._resolve_refs(obj) if isinstance(obj, dict) else {}

    def _validate_body(self, body: Any, schema: dict) -> list[str]:
        if not schema:
            return []
        try:
            from jsonschema import Draft7Validator

            effective = dict(schema)
            if self._strict:
                effective["additionalProperties"] = False
            validator = Draft7Validator(effective)
            return [e.message for e in validator.iter_errors(body)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[contract] OpenAPI validation error: %s", exc)
            return [str(exc)]
