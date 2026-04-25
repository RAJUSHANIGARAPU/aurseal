"""Provider contract validation.

Validates live HTTP responses against a stored consumer contract.
Called during test execution (provider side) to verify the provider
still honours what consumers expect.

Validation modes:
  lenient — only required fields and their types are checked. Extra fields
            in the response are allowed. This is the default and recommended
            mode for most scenarios.
  strict  — no extra fields allowed in the response. Only use when the
            consumer is highly sensitive to response shape changes.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from contract_testing.models import Contract, Interaction, ValidationResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"/[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_NUM_SEG_RE = re.compile(r"/\d+")


def _normalize_path(path: str) -> str:
    path = _UUID_RE.sub("/{id}", path)
    path = _NUM_SEG_RE.sub("/{id}", path)
    return path


class ProviderContractValidator:
    """Validates provider responses against a consumer contract."""

    def __init__(self, contract: Contract, validation_mode: str = "lenient") -> None:
        self._contract = contract
        self._strict = validation_mode == "strict"
        self._index = {i.key: i for i in contract.interactions}

    def validate_response(
        self,
        method: str,
        path: str,
        status: int,
        response_body: Any,
    ) -> ValidationResult:
        """Validate a single provider response against the matching contract interaction.

        Returns a passing ValidationResult if no matching interaction is found
        (the contract does not define expectations for this endpoint).
        """
        norm_path = _normalize_path(path)
        key = f"{method.upper()} {norm_path}"
        interaction = self._index.get(key)

        if interaction is None:
            return ValidationResult(
                interaction_key=key, passed=True, errors=["(no contract for this endpoint)"]
            )

        errors = self._compare(interaction, status, response_body)
        return ValidationResult(
            interaction_key=key,
            passed=not errors,
            errors=errors,
        )

    def validate_all(self, responses: list[dict]) -> list[ValidationResult]:
        """Validate a batch of {method, path, status, body} dicts."""
        return [
            self.validate_response(
                r.get("method", "GET"),
                r.get("path", "/"),
                r.get("status", 200),
                r.get("body"),
            )
            for r in responses
        ]

    # ------------------------------------------------------------------
    # Internal comparison logic
    # ------------------------------------------------------------------

    def _compare(self, interaction: Interaction, status: int, body: Any) -> list[str]:
        errors: list[str] = []

        # 1. Status code
        expected_status = interaction.response.status
        if expected_status and status != expected_status:
            errors.append(f"Status mismatch: expected {expected_status}, got {status}")

        # 2. Response body schema
        schema = interaction.response.body_schema
        if schema and body is not None:
            errors.extend(self._validate_body(body, schema))

        return errors

    def _validate_body(self, body: Any, schema: dict) -> list[str]:
        """Validate body against a JSON Schema using jsonschema library."""
        if not schema:
            return []
        try:
            from jsonschema import Draft7Validator

            effective = dict(schema)
            if self._strict:
                effective["additionalProperties"] = False
            else:
                effective.pop("additionalProperties", None)

            validator = Draft7Validator(effective)
            return [e.message for e in validator.iter_errors(body)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[contract] validation error: %s", exc)
            return [str(exc)]
