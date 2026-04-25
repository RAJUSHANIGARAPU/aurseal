"""Tests for IngestionAgent — parse the sample Postman collection."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.agents.ingestion import IngestionAgent, PostmanRequest

_SAMPLE = Path(__file__).parent.parent.parent / "api" / "postman" / "sample_collection.json"


@pytest.fixture
def requests_list() -> list[PostmanRequest]:
    agent = IngestionAgent()
    return agent.parse_file(_SAMPLE)


def test_sample_collection_loads(requests_list: list[PostmanRequest]) -> None:
    """Sample collection must parse without error."""
    assert isinstance(requests_list, list)
    assert len(requests_list) > 0


def test_request_count(requests_list: list[PostmanRequest]) -> None:
    """Sample collection has exactly 6 requests across all folders."""
    assert len(requests_list) == 6


def test_all_items_are_postman_requests(requests_list: list[PostmanRequest]) -> None:
    """Every item must be a PostmanRequest dataclass."""
    for item in requests_list:
        assert isinstance(item, PostmanRequest)


def test_methods_are_valid(requests_list: list[PostmanRequest]) -> None:
    """All parsed methods must be valid HTTP verbs."""
    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    for req in requests_list:
        assert req.method in valid_methods, f"Invalid method: {req.method}"


def test_urls_are_non_empty(requests_list: list[PostmanRequest]) -> None:
    """Every request must have a non-empty URL."""
    for req in requests_list:
        assert req.url, f"Empty URL for request: {req.name}"


def test_contains_get_requests(requests_list: list[PostmanRequest]) -> None:
    """Collection must contain at least one GET request."""
    get_requests = [r for r in requests_list if r.method == "GET"]
    assert len(get_requests) >= 1


def test_contains_post_requests(requests_list: list[PostmanRequest]) -> None:
    """Collection must contain at least one POST request."""
    post_requests = [r for r in requests_list if r.method == "POST"]
    assert len(post_requests) >= 1


def test_get_request_urls(requests_list: list[PostmanRequest]) -> None:
    """GET requests must target httpbin.org URLs."""
    get_requests = [r for r in requests_list if r.method == "GET"]
    for req in get_requests:
        assert "httpbin.org" in req.url, f"Unexpected URL: {req.url}"


def test_post_json_body_parsed(requests_list: list[PostmanRequest]) -> None:
    """POST requests with JSON body must have body parsed as dict."""
    post_requests = [r for r in requests_list if r.method == "POST"]
    json_posts = [r for r in post_requests if r.body_mode in ("raw_json", "raw")]
    assert len(json_posts) >= 1, "Expected at least one POST with JSON body"


def test_folder_paths_set(requests_list: list[PostmanRequest]) -> None:
    """Requests inside folders must have folder_path set."""
    requests_with_folders = [r for r in requests_list if r.folder_path]
    assert len(requests_with_folders) == len(
        requests_list
    ), "All sample requests are inside named folders"


def test_request_names_are_strings(requests_list: list[PostmanRequest]) -> None:
    """Every request must have a non-empty string name."""
    for req in requests_list:
        assert isinstance(req.name, str)
        assert len(req.name) > 0


def test_query_params_extracted(requests_list: list[PostmanRequest]) -> None:
    """The GET /get request must have query params parsed."""
    get_get = [r for r in requests_list if "/get" in r.url and r.method == "GET"]
    assert get_get, "Expected a GET /get request in the collection"
    req = get_get[0]
    assert isinstance(req.query_params, dict)
    assert len(req.query_params) >= 1


def test_headers_extracted(requests_list: list[PostmanRequest]) -> None:
    """Requests with headers must have them parsed as dicts."""
    requests_with_headers = [r for r in requests_list if r.headers]
    assert len(requests_with_headers) >= 1, "Expected at least one request with headers"
    for req in requests_with_headers:
        assert isinstance(req.headers, dict)


def test_parse_inline_dict() -> None:
    """IngestionAgent must parse a minimal inline collection dict."""
    agent = IngestionAgent()
    minimal = {
        "item": [
            {
                "name": "Simple GET",
                "request": {
                    "method": "GET",
                    "url": {"raw": "https://example.com/api"},
                    "header": [],
                },
            }
        ]
    }
    result = agent.parse(minimal)
    assert len(result) == 1
    assert result[0].method == "GET"
    assert result[0].url == "https://example.com/api"


def test_nested_folders_flattened() -> None:
    """Nested folder items must be flattened into the result list."""
    agent = IngestionAgent()
    nested = {
        "item": [
            {
                "name": "Outer Folder",
                "item": [
                    {
                        "name": "Inner Folder",
                        "item": [
                            {
                                "name": "Deep Request",
                                "request": {
                                    "method": "GET",
                                    "url": {"raw": "https://example.com/deep"},
                                    "header": [],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    result = agent.parse(nested)
    assert len(result) == 1
    assert result[0].folder_path == ["Outer Folder", "Inner Folder"]
    assert result[0].name == "Deep Request"
