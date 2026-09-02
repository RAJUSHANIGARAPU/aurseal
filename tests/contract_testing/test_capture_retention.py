"""
How much a session holds on to.

Request bodies were clipped at 200 chars; response bodies were not clipped at
all, and every interaction was kept until ``pytest_sessionfinish``, where
``from_captures`` finally threw the duplicates away. Ten thousand calls to one
endpoint held ten thousand full response bodies in order to build one
interaction, and a five-hundred-test run held every test's captures at once.

What a contract needs from a response is its *shape* — ``infer_schema`` reads
types and keys, and only the first element of a list. So the fix bounds the
count (dedupe where the duplicate arrives, not at the end) and bounds the size
(clip strings and long lists, never keys).

The last class is the control: bounding is trivially "achieved" by keeping
nothing, so the generated contract must come out identical to the one the
unbounded body produced.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from contract_testing import capture
from contract_testing.config import ContractConfig
from contract_testing.consumer import ConsumerContractGenerator
from contract_testing.pytest_plugin import ContractPlugin
from tests.contract_testing._fake_http import FakeHttp, fire


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(requests.Session, "send", fake.send, raising=True)
    capture.install()
    try:
        yield fake
    finally:
        capture.deactivate()
        capture.remove()


def _config(tmp_path: Path) -> ContractConfig:
    return ContractConfig(
        enabled=True,
        mode="consumer",
        contracts_dir=tmp_path / "contracts",
        consumer_name="wiki",
        provider_name="api",
    )


def _raw(path: str = "/users") -> dict:
    return {
        "method": "GET",
        "path": path,
        "query": "",
        "request_headers": {},
        "request_body": None,
        "status": 200,
        "response_headers": {"content-type": "application/json"},
        "response_body": {"id": 1},
        "test_name": "tests/t.py::test_x",
    }


class TestRepeatsDoNotAccumulate:
    def test_the_same_call_a_thousand_times_is_kept_once(self, http):
        capture.activate(test_name="tests/t.py::test_hammer")
        for _ in range(1000):
            fire("/users")

        interactions = capture.deactivate()
        assert http.sent == 1000, "the calls did not happen — the test proves nothing"
        assert len(interactions) == 1

    def test_the_repeats_are_counted_not_forgotten(self, http):
        capture.activate(test_name="tests/t.py::test_hammer")
        for _ in range(1000):
            fire("/users")
        assert capture.deactivate()[0]["occurrences"] == 1000

    def test_ids_in_the_path_collapse_onto_one_interaction(self, http):
        """``normalize_path`` is what the contract keys on, so dedup must too."""
        capture.activate(test_name="tests/t.py::test_ids")
        for i in range(500):
            fire(f"/users/{i}")
        assert len(capture.deactivate()) == 1

    def test_a_different_status_is_kept_separately(self, http):
        """A 500 on an endpoint that usually 200s is signal, not a duplicate."""
        capture.activate(test_name="tests/t.py::test_status")
        fire("/users")
        http.status = 500
        fire("/users")
        assert sorted(i["status"] for i in capture.deactivate()) == [200, 500]

    def test_a_session_of_many_tests_does_not_stack_up(self, tmp_path):
        plugin = ContractPlugin(_config(tmp_path))
        with patch("contract_testing.capture.deactivate", return_value=[_raw()]):
            for _ in range(500):
                plugin.pytest_runtest_teardown(item=None, nextitem=None)
        assert len(plugin._raw_captures) == 1


class TestOneBodyIsBounded:
    def test_a_huge_string_is_clipped(self, http):
        http.payload = {"html": "x" * 5_000_000}
        capture.activate(test_name="tests/t.py::test_big")
        fire("/page")
        kept = capture.deactivate()[0]["response_body"]["html"]
        assert len(kept) < 10_000

    def test_a_huge_list_is_clipped(self, http):
        http.payload = {"rows": [{"id": i} for i in range(100_000)]}
        capture.activate(test_name="tests/t.py::test_rows")
        fire("/rows")
        assert len(capture.deactivate()[0]["response_body"]["rows"]) < 1_000

    def test_a_huge_string_nested_in_a_list_is_clipped(self, http):
        http.payload = {"rows": [{"blob": "y" * 5_000_000}]}
        capture.activate(test_name="tests/t.py::test_nested")
        fire("/rows")
        kept = capture.deactivate()[0]["response_body"]["rows"][0]["blob"]
        assert len(kept) < 10_000

    def test_a_huge_request_body_is_clipped(self, http):
        capture.activate(test_name="tests/t.py::test_upload")
        fire("/upload", method="POST", body={"doc": "z" * 5_000_000})
        assert len(capture.deactivate()[0]["request_body"]["doc"]) < 10_000


class TestTheContractIsUnchanged:
    """
    Control. Every assertion above is satisfied by a capture that stores
    nothing, so what actually matters is that the contract built from the
    bounded capture is the one the full body would have produced.
    """

    def _interactions(self, captures):
        """The contract's interactions — ``created_at`` is a clock, not a claim."""
        generator = ConsumerContractGenerator(consumer="wiki", provider="api")
        return generator.from_captures(captures).to_dict()["interactions"]

    def test_an_ordinary_body_is_untouched(self, http):
        http.payload = {"id": 1, "name": "ada", "tags": ["a", "b"], "active": True}
        capture.activate(test_name="tests/t.py::test_plain")
        fire("/users")
        assert capture.deactivate()[0]["response_body"] == http.payload

    def test_a_clipped_body_yields_the_same_schema(self, http):
        full = {"rows": [{"id": i, "blob": "y" * 100_000} for i in range(5_000)], "total": 5_000}
        http.payload = full
        capture.activate(test_name="tests/t.py::test_schema")
        fire("/rows")
        bounded = capture.deactivate()

        unbounded = [dict(bounded[0], response_body=full)]
        assert self._interactions(bounded) == self._interactions(unbounded)

    def test_every_distinct_endpoint_survives(self, http):
        capture.activate(test_name="tests/t.py::test_many")
        for name in ("/users", "/orders", "/reports"):
            for _ in range(20):
                fire(name)
        assert sorted(i["path"] for i in capture.deactivate()) == [
            "/orders",
            "/reports",
            "/users",
        ]

    def test_the_plugin_still_builds_every_interaction(self, tmp_path):
        plugin = ContractPlugin(_config(tmp_path))
        batch = [_raw("/users"), _raw("/orders"), _raw("/users")]
        with patch("contract_testing.capture.deactivate", return_value=batch):
            plugin.pytest_runtest_teardown(item=None, nextitem=None)
        assert sorted(r["path"] for r in plugin._raw_captures) == ["/orders", "/users"]
