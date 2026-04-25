"""Tests for contract_testing.consumer."""

from __future__ import annotations

from dataclasses import dataclass, field

from contract_testing.consumer import ConsumerContractGenerator, infer_schema, normalize_path


@dataclass
class FakeRequest:
    name: str = "Get Users"
    method: str = "GET"
    url: str = "https://api.example.com/users"
    headers: dict = field(default_factory=dict)
    query_params: dict = field(default_factory=dict)
    body: dict | None = None
    body_mode: str = "none"
    folder_path: list = field(default_factory=list)
    pre_request_script: str = ""
    test_script: str = ""


def _generator() -> ConsumerContractGenerator:
    return ConsumerContractGenerator(consumer="test_consumer", provider="test_provider")


# ------------------------------------------------------------------
# normalize_path
# ------------------------------------------------------------------


def test_normalize_path_strips_query_params():
    assert "?" not in normalize_path("https://api.example.com/users?page=1")


def test_normalize_path_replaces_numeric_segments():
    result = normalize_path("/users/42/orders")
    assert "42" not in result
    assert "{id}" in result


def test_normalize_path_replaces_uuids():
    result = normalize_path("/items/550e8400-e29b-41d4-a716-446655440000")
    assert "550e8400" not in result
    assert "{id}" in result


def test_normalize_path_returns_root_for_empty():
    assert normalize_path("") == "/"


def test_normalize_path_extracts_path_from_full_url():
    result = normalize_path("https://api.example.com/users/123")
    assert result.startswith("/")


# ------------------------------------------------------------------
# infer_schema
# ------------------------------------------------------------------


def test_infer_schema_string():
    assert infer_schema("hello") == {"type": "string"}


def test_infer_schema_integer():
    assert infer_schema(42) == {"type": "integer"}


def test_infer_schema_float():
    assert infer_schema(3.14) == {"type": "number"}


def test_infer_schema_boolean():
    assert infer_schema(True) == {"type": "boolean"}


def test_infer_schema_null():
    schema = infer_schema(None)
    assert "null" in schema.get("type", [])


def test_infer_schema_list_empty():
    schema = infer_schema([])
    assert schema["type"] == "array"


def test_infer_schema_list_with_items():
    schema = infer_schema([{"id": 1}])
    assert schema["type"] == "array"
    assert "items" in schema


def test_infer_schema_object():
    schema = infer_schema({"id": 1, "name": "Alice"})
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"id", "name"}
    assert "id" in schema["properties"]
    assert "name" in schema["properties"]


def test_infer_schema_nested_object():
    schema = infer_schema({"user": {"id": 1, "email": "a@b.com"}})
    assert schema["properties"]["user"]["type"] == "object"
    assert "id" in schema["properties"]["user"]["required"]


# ------------------------------------------------------------------
# from_postman_requests
# ------------------------------------------------------------------


def test_from_postman_requests_creates_interactions():
    g = _generator()
    reqs = [FakeRequest(method="GET", url="https://api.example.com/users")]
    contract = g.from_postman_requests(reqs)
    assert len(contract.interactions) == 1


def test_from_postman_requests_sets_consumer_provider():
    g = _generator()
    contract = g.from_postman_requests([FakeRequest()])
    assert contract.consumer == "test_consumer"
    assert contract.provider == "test_provider"


def test_from_postman_requests_deduplicates_same_path():
    g = _generator()
    reqs = [
        FakeRequest(name="First", method="GET", url="https://api.example.com/users"),
        FakeRequest(name="Second", method="GET", url="https://api.example.com/users"),
    ]
    contract = g.from_postman_requests(reqs)
    assert len(contract.interactions) == 1


def test_from_postman_requests_includes_body_schema():
    g = _generator()
    req = FakeRequest(method="POST", body={"name": "Alice"}, url="https://api.example.com/users")
    contract = g.from_postman_requests([req])
    assert contract.interactions[0].request.body_schema.get("type") == "object"


# ------------------------------------------------------------------
# from_captures
# ------------------------------------------------------------------


def _raw(method="GET", path="/users", status=200, body=None):
    return {
        "method": method,
        "path": path,
        "query": "",
        "request_headers": {},
        "request_body": None,
        "status": status,
        "response_headers": {"content-type": "application/json"},
        "response_body": body or {"results": [], "total": 0},
        "test_name": "test_example",
    }


def test_from_captures_creates_interactions():
    g = _generator()
    contract = g.from_captures([_raw()])
    assert len(contract.interactions) == 1


def test_from_captures_infers_response_schema():
    g = _generator()
    contract = g.from_captures([_raw(body={"id": 1, "name": "Alice"})])
    schema = contract.interactions[0].response.body_schema
    assert schema.get("type") == "object"
    assert "id" in schema.get("required", [])


def test_from_captures_deduplicates_by_method_path():
    g = _generator()
    raws = [_raw(path="/users"), _raw(path="/users")]
    contract = g.from_captures(raws)
    assert len(contract.interactions) == 1


def test_from_captures_strips_auth_headers():
    g = _generator()
    raw = _raw()
    raw["request_headers"] = {"Authorization": "Bearer tok", "Accept": "application/json"}
    contract = g.from_captures([raw])
    headers = contract.interactions[0].request.headers
    assert "Authorization" not in headers
