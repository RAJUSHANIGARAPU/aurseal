"""Tests for contract_testing.provider."""

from __future__ import annotations

from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.provider import ProviderContractValidator


def _contract(interactions=None) -> Contract:
    return Contract(
        consumer="consumer",
        provider="provider",
        interactions=interactions or [_interaction()],
    )


def _interaction(
    method="GET",
    path="/users",
    status=200,
    body_schema=None,
) -> Interaction:
    return Interaction(
        description=f"{method} {path}",
        request=RequestSchema(method=method, path=path),
        response=ResponseSchema(
            status=status,
            body_schema=body_schema
            or {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        ),
    )


# ------------------------------------------------------------------
# validate_response — passing cases
# ------------------------------------------------------------------


def test_validate_passes_when_body_matches():
    validator = ProviderContractValidator(_contract())
    result = validator.validate_response("GET", "/users", 200, {"id": 1, "name": "Alice"})
    assert result.passed


def test_validate_passes_with_extra_fields_in_lenient_mode():
    validator = ProviderContractValidator(_contract(), validation_mode="lenient")
    result = validator.validate_response(
        "GET", "/users", 200, {"id": 1, "name": "Alice", "extra": "field"}
    )
    assert result.passed


def test_validate_passes_when_no_contract_for_endpoint():
    validator = ProviderContractValidator(_contract())
    result = validator.validate_response("GET", "/unknown", 200, {})
    assert result.passed  # no contract = no failure


# ------------------------------------------------------------------
# validate_response — failing cases
# ------------------------------------------------------------------


def test_validate_fails_on_status_mismatch():
    validator = ProviderContractValidator(_contract())
    result = validator.validate_response("GET", "/users", 404, {"id": 1, "name": "Alice"})
    assert not result.passed
    assert any("Status" in e for e in result.errors)


def test_validate_fails_when_required_field_missing():
    validator = ProviderContractValidator(_contract())
    result = validator.validate_response("GET", "/users", 200, {"id": 1})
    assert not result.passed
    assert any("name" in e for e in result.errors)


def test_validate_fails_on_type_mismatch():
    validator = ProviderContractValidator(_contract())
    result = validator.validate_response("GET", "/users", 200, {"id": "not-an-int", "name": "x"})
    assert not result.passed


def test_validate_fails_extra_fields_in_strict_mode():
    validator = ProviderContractValidator(_contract(), validation_mode="strict")
    result = validator.validate_response(
        "GET", "/users", 200, {"id": 1, "name": "Alice", "extra": "field"}
    )
    assert not result.passed


# ------------------------------------------------------------------
# Path normalisation (IDs should match template)
# ------------------------------------------------------------------


def test_validates_path_with_numeric_id():
    i = _interaction(path="/{id}")
    validator = ProviderContractValidator(_contract([i]))
    result = validator.validate_response("GET", "/42", 200, {"id": 42, "name": "Bob"})
    assert result.passed


# ------------------------------------------------------------------
# validate_all
# ------------------------------------------------------------------


def test_validate_all_returns_results_for_each_response():
    validator = ProviderContractValidator(_contract())
    responses = [
        {"method": "GET", "path": "/users", "status": 200, "body": {"id": 1, "name": "A"}},
        {"method": "GET", "path": "/users", "status": 200, "body": {"id": 2, "name": "B"}},
    ]
    results = validator.validate_all(responses)
    assert len(results) == 2
    assert all(r.passed for r in results)


def test_validate_all_empty_returns_empty():
    validator = ProviderContractValidator(_contract())
    assert validator.validate_all([]) == []
