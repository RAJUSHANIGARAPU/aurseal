"""Tests for contract_testing.storage."""

from __future__ import annotations

import json

import pytest

from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.storage import ContractStore


def _contract(
    consumer: str = "c",
    provider: str = "p",
    version: str = "1.0.0",
) -> Contract:
    return Contract(
        consumer=consumer,
        provider=provider,
        interactions=[
            Interaction(
                description="GET /users",
                request=RequestSchema(method="GET", path="/users"),
                response=ResponseSchema(status=200, body_schema={"type": "object"}),
            )
        ],
        version=version,
    )


@pytest.fixture()
def store(tmp_path):
    return ContractStore(tmp_path / "contracts")


# ------------------------------------------------------------------
# save / load_latest
# ------------------------------------------------------------------


def test_save_creates_version_file(store, tmp_path):
    c = _contract()
    path = store.save(c)
    assert path.exists()
    assert path.name == "v1.0.0.json"


def test_save_creates_latest_file(store, tmp_path):
    c = _contract()
    store.save(c)
    latest = tmp_path / "contracts" / "c___p" / "latest.json"
    assert latest.exists()


def test_load_latest_returns_contract(store):
    c = _contract()
    store.save(c)
    loaded = store.load_latest("c", "p")
    assert loaded is not None
    assert loaded.consumer == "c"
    assert loaded.provider == "p"
    assert loaded.version == "1.0.0"


def test_load_latest_returns_none_when_missing(store):
    assert store.load_latest("nobody", "nobody") is None


def test_load_latest_reflects_most_recent_save(store):
    store.save(_contract(version="1.0.0"))
    store.save(_contract(version="2.0.0"))
    loaded = store.load_latest("c", "p")
    assert loaded is not None
    assert loaded.version == "2.0.0"


# ------------------------------------------------------------------
# load_version
# ------------------------------------------------------------------


def test_load_specific_version(store):
    store.save(_contract(version="1.0.0"))
    store.save(_contract(version="2.0.0"))
    v1 = store.load_version("c", "p", "1.0.0")
    assert v1 is not None
    assert v1.version == "1.0.0"


def test_load_version_missing_returns_none(store):
    store.save(_contract(version="1.0.0"))
    assert store.load_version("c", "p", "9.9.9") is None


# ------------------------------------------------------------------
# list_versions
# ------------------------------------------------------------------


def test_list_versions_returns_all(store):
    store.save(_contract(version="1.0.0"))
    store.save(_contract(version="1.1.0"))
    versions = store.list_versions("c", "p")
    assert "1.0.0" in versions
    assert "1.1.0" in versions


def test_list_versions_newest_first(store):
    store.save(_contract(version="1.0.0"))
    store.save(_contract(version="1.1.0"))
    versions = store.list_versions("c", "p")
    assert versions[0] == "1.1.0"


def test_list_versions_empty_when_nothing_saved(store):
    assert store.list_versions("nobody", "nobody") == []


# ------------------------------------------------------------------
# exists
# ------------------------------------------------------------------


def test_exists_true_after_save(store):
    store.save(_contract())
    assert store.exists("c", "p")


def test_exists_false_before_save(store):
    assert not store.exists("c", "p")


# ------------------------------------------------------------------
# Idempotent save (same version twice)
# ------------------------------------------------------------------


def test_idempotent_save_does_not_duplicate_index_entry(store):
    store.save(_contract(version="1.0.0"))
    store.save(_contract(version="1.0.0"))
    versions = store.list_versions("c", "p")
    assert versions.count("1.0.0") == 1


# ------------------------------------------------------------------
# Index file content
# ------------------------------------------------------------------


def test_index_contains_consumer_and_provider(store, tmp_path):
    store.save(_contract())
    index_path = tmp_path / "contracts" / "c___p" / "index.json"
    data = json.loads(index_path.read_text())
    assert data[0]["consumer"] == "c"
    assert data[0]["provider"] == "p"


# ------------------------------------------------------------------
# Stored content is valid JSON
# ------------------------------------------------------------------


def test_saved_file_is_valid_json(store, tmp_path):
    store.save(_contract())
    path = tmp_path / "contracts" / "c___p" / "v1.0.0.json"
    data = json.loads(path.read_text())
    assert data["consumer"]["name"] == "c"
