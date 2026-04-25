"""Validation engine: status, response time, JSON schema, headers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import jsonschema
import requests


@dataclass
class ValidationRules:
    """Rules applied to an HTTP response."""

    expected_status: int | None = None
    max_response_time_ms: int | None = None
    json_schema: dict | None = None
    required_headers: dict[str, str] | None = None
    custom_assertions: list[str] | None = None  # reserved for future JSONPath expressions


@dataclass
class ValidationResult:
    """Outcome of running ValidationRules against an HTTP response."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class ValidationEngine:
    """Validates HTTP responses against a set of declarative rules."""

    def validate(
        self,
        response: requests.Response,
        rules: ValidationRules,
        duration_ms: float | None = None,
    ) -> ValidationResult:
        """Run all configured rules and aggregate failures.

        Args:
            response: The requests.Response to inspect.
            rules: The rules to apply.
            duration_ms: Elapsed time for the request in milliseconds.
                         If omitted, it is read from response.elapsed.

        Returns:
            ValidationResult with passed=True only when all rules pass.
        """
        failures: list[str] = []
        elapsed = duration_ms if duration_ms is not None else _elapsed_ms(response)

        if rules.expected_status is not None:
            if response.status_code != rules.expected_status:
                failures.append(
                    f"Expected status {rules.expected_status}, got {response.status_code}"
                )

        if rules.max_response_time_ms is not None:
            if elapsed > rules.max_response_time_ms:
                failures.append(
                    f"Response time {elapsed:.0f}ms exceeds limit {rules.max_response_time_ms}ms"
                )

        if rules.required_headers:
            for header_name, expected_value in rules.required_headers.items():
                actual = response.headers.get(header_name, "")
                if expected_value.lower() not in actual.lower():
                    failures.append(
                        f"Header '{header_name}': expected '{expected_value}', got '{actual}'"
                    )

        if rules.json_schema is not None:
            schema_failures = self._validate_schema(response, rules.json_schema)
            failures.extend(schema_failures)

        return ValidationResult(
            passed=len(failures) == 0,
            failures=failures,
            duration_ms=elapsed,
        )

    def _validate_schema(self, response: requests.Response, schema: dict) -> list[str]:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            return ["Response body is not valid JSON"]

        try:
            jsonschema.validate(instance=body, schema=schema)
            return []
        except jsonschema.ValidationError as exc:
            return [f"JSON schema violation: {exc.message}"]
        except jsonschema.SchemaError as exc:
            return [f"Invalid JSON schema definition: {exc.message}"]


def _elapsed_ms(response: requests.Response) -> float:
    """Extract elapsed time from a requests.Response in milliseconds."""
    try:
        return response.elapsed.total_seconds() * 1000
    except AttributeError:
        return 0.0


def measure_request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
) -> tuple[requests.Response, float]:
    """Perform an HTTP request and return (response, duration_ms)."""
    start = time.monotonic()
    resp = requests.request(
        method=method,
        url=url,
        headers=headers or {},
        json=json,
        params=params,
        timeout=timeout,
    )
    duration_ms = (time.monotonic() - start) * 1000
    return resp, duration_ms
