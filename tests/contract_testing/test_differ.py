"""Tests for contract_testing.differ."""

from __future__ import annotations

from contract_testing.differ import ContractDiffer
from contract_testing.models import (
    Contract,
    Interaction,
    RequestSchema,
    ResponseSchema,
)


def _contract(interactions: list[Interaction], version: str = "1.0.0") -> Contract:
    return Contract(
        consumer="consumer",
        provider="provider",
        interactions=interactions,
        version=version,
    )


def _interaction(
    method: str = "GET",
    path: str = "/users",
    status: int = 200,
    body_schema: dict | None = None,
    required: list[str] | None = None,
    properties: dict | None = None,
) -> Interaction:
    if body_schema is None:
        req = required or ["id", "name"]
        props = properties or {
            "id": {"type": "integer"},
            "name": {"type": "string"},
        }
        body_schema = {"type": "object", "required": req, "properties": props}
    return Interaction(
        description=f"{method} {path}",
        request=RequestSchema(method=method, path=path),
        response=ResponseSchema(status=status, body_schema=body_schema),
    )


# ------------------------------------------------------------------
# No changes
# ------------------------------------------------------------------


def test_no_diff_identical_contracts():
    i = _interaction()
    diff = ContractDiffer().diff(_contract([i]), _contract([i]))
    assert diff.change_type.value == "none"
    assert not diff.breaking
    assert not diff.non_breaking


# ------------------------------------------------------------------
# Breaking changes
# ------------------------------------------------------------------


def test_removed_interaction_is_breaking():
    old = _contract([_interaction(path="/users"), _interaction(path="/orders")])
    new = _contract([_interaction(path="/users")])
    diff = ContractDiffer().diff(old, new)
    assert diff.has_breaking
    assert any("/orders" in c for c in diff.breaking)


def test_status_code_change_is_breaking():
    old = _contract([_interaction(status=200)])
    new = _contract([_interaction(status=201)])
    diff = ContractDiffer().diff(old, new)
    assert diff.has_breaking
    assert any("Status code" in c for c in diff.breaking)


def test_required_field_removed_is_breaking():
    old = _contract([_interaction(required=["id", "name"])])
    new = _contract([_interaction(required=["id"])])
    diff = ContractDiffer().diff(old, new)
    assert diff.has_breaking
    assert any("name" in c for c in diff.breaking)


def test_field_type_change_is_breaking():
    old = _contract([_interaction(required=["id"], properties={"id": {"type": "integer"}})])
    new = _contract([_interaction(required=["id"], properties={"id": {"type": "string"}})])
    diff = ContractDiffer().diff(old, new)
    assert diff.has_breaking
    assert any("'id'" in c for c in diff.breaking)


def test_top_level_type_change_is_breaking():
    old = _contract(
        [_interaction(body_schema={"type": "object", "required": [], "properties": {}})]
    )
    new = _contract([_interaction(body_schema={"type": "array", "items": {"type": "object"}})])
    diff = ContractDiffer().diff(old, new)
    assert diff.has_breaking


# ------------------------------------------------------------------
# Non-breaking changes
# ------------------------------------------------------------------


def test_new_interaction_is_non_breaking():
    old = _contract([_interaction(path="/users")])
    new = _contract([_interaction(path="/users"), _interaction(path="/orders")])
    diff = ContractDiffer().diff(old, new)
    assert not diff.has_breaking
    assert any("/orders" in c for c in diff.non_breaking)


def test_new_required_field_in_response_is_non_breaking():
    old = _contract([_interaction(required=["id"])])
    new = _contract([_interaction(required=["id", "email"])])
    diff = ContractDiffer().diff(old, new)
    assert not diff.has_breaking
    assert any("email" in c for c in diff.non_breaking)


# ------------------------------------------------------------------
# Nested objects
# ------------------------------------------------------------------


def test_nested_field_type_change_is_breaking():
    old_schema = {
        "type": "object",
        "required": ["user"],
        "properties": {
            "user": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            }
        },
    }
    new_schema = {
        "type": "object",
        "required": ["user"],
        "properties": {
            "user": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }
        },
    }
    old = _contract([_interaction(body_schema=old_schema)])
    new = _contract([_interaction(body_schema=new_schema)])
    diff = ContractDiffer().diff(old, new)
    assert diff.has_breaking


# ------------------------------------------------------------------
# Semver versioning
# ------------------------------------------------------------------


def test_next_version_breaking_bumps_major():
    old = _contract([_interaction(path="/users"), _interaction(path="/orders")])
    new = _contract([_interaction(path="/users")])
    diff = ContractDiffer().diff(old, new)
    assert diff.next_version("1.2.3") == "2.0.0"


def test_next_version_non_breaking_bumps_minor():
    old = _contract([_interaction(path="/users")])
    new = _contract([_interaction(path="/users"), _interaction(path="/orders")])
    diff = ContractDiffer().diff(old, new)
    assert diff.next_version("1.2.3") == "1.3.0"


def test_next_version_no_change_bumps_patch():
    i = _interaction()
    diff = ContractDiffer().diff(_contract([i]), _contract([i]))
    assert diff.next_version("1.2.3") == "1.2.4"


# ------------------------------------------------------------------
# Empty contracts
# ------------------------------------------------------------------


def test_both_empty_no_diff():
    diff = ContractDiffer().diff(_contract([]), _contract([]))
    assert diff.change_type.value == "none"


def test_old_empty_new_has_interactions_non_breaking():
    new = _contract([_interaction()])
    diff = ContractDiffer().diff(_contract([]), new)
    assert not diff.has_breaking
    assert diff.non_breaking
