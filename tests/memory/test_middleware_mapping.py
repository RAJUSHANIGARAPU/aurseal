"""
Matching a pytest nodeid back to the Postman request it was generated from.

``MemoryMiddleware`` indexed the requests by ``PostmanRequest.name`` — the item
title out of the collection, "Get User By ID" — and looked them up by
``FailureAnalysis.test_name``, which is a pytest nodeid,
``generated_tests/test_users.py::test_get_user_by_id``. Those two strings can
never be equal, so every lookup missed and the whole memory layer ran on
nothing: every record stored ``endpoint=""``, ``method=""`` and an empty
payload snippet, and an empty endpoint makes ``MemoryStore.query`` drop its
endpoint filter, so insights were ranked against every unrelated record in the
database. ``before_execution`` had the same mismatch, which made
``MEMORY_MODE=active`` a permanent no-op.

The nodeid is not guesswork — ``GenerationAgent`` builds both halves of it, the
file from the folder path and the function from the request name — so the tests
here derive the nodeids by reading the files the generator actually wrote,
rather than by restating the naming rule. If the generator's naming changes and
the mapping does not follow, these go red.
"""

from __future__ import annotations

import pytest

from api.agents.analysis import FailureAnalysis, FailureCategory
from api.agents.generation import GenerationAgent
from api.agents.ingestion import PostmanRequest
from api.engine.context_memory import ContextMemory
from memory.config import MemoryConfig
from memory.middleware import MemoryMiddleware, nodeid_suffix
from memory.retriever import MemoryRetriever
from memory.store import MemoryStore

RUN_ID = "run-1"


def _request(name: str, url: str, folder: str = "Users", method: str = "GET") -> PostmanRequest:
    return PostmanRequest(
        name=name,
        method=method,
        url=url,
        body={"role": "admin"},
        body_mode="raw_json",
        folder_path=[folder],
    )


def _failure(nodeid: str) -> FailureAnalysis:
    return FailureAnalysis(
        test_name=nodeid,
        category=FailureCategory.ASSERTION_ERROR,
        root_cause="Expected 200, got 404",
        suggested_fix="Check the path",
        raw_message="AssertionError: expected 200 got 404",
    )


def _nodeids(requests: list[PostmanRequest], out_dir) -> list[str]:
    """Generate the real test files and read the nodeids back out of them.

    Nothing here restates the generator's slug rules — if they change, the
    nodeids these tests use change with them.
    """
    files = GenerationAgent(output_dir=out_dir).generate(requests)
    found: list[str] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("def test_"):
                fn = line[len("def ") :].split("(")[0]
                found.append(f"{path.as_posix()}::{fn}")
    return found


def _one(nodeids: list[str], function: str) -> str:
    matches = [n for n in nodeids if n.endswith(f"::{function}")]
    assert len(matches) == 1, f"expected one {function}, got {matches}"
    return matches[0]


@pytest.fixture
def middleware(tmp_path):
    def _build(mode: str = "passive") -> MemoryMiddleware:
        config = MemoryConfig(
            enabled=True,
            mode=mode,
            db_path=tmp_path / "memory.db",
            llm_enabled=False,
        )
        store = MemoryStore(config)
        return MemoryMiddleware(store, MemoryRetriever(), config)

    return _build


class TestNodeidSuffix:
    """The part of a nodeid the generator controls: the file name and the function."""

    def test_a_plain_nodeid(self):
        assert (
            nodeid_suffix("generated_tests/test_users.py::test_get_user_by_id")
            == "test_users.py::test_get_user_by_id"
        )

    def test_a_bare_file_name(self):
        assert nodeid_suffix("test_users.py::test_x") == "test_users.py::test_x"

    def test_a_parametrised_case_belongs_to_its_function(self):
        """``[...]`` is one case of a test, not a different test."""
        assert nodeid_suffix("d/test_u.py::test_x[a-1]") == "test_u.py::test_x"

    def test_a_windows_path_separator(self):
        assert nodeid_suffix("d\\test_u.py::test_x") == "test_u.py::test_x"

    def test_a_method_on_a_class(self):
        assert nodeid_suffix("d/test_u.py::TestU::test_x") == "test_u.py::test_x"

    @pytest.mark.parametrize("value", ["", "Get User By ID", "test_users.py"])
    def test_something_that_is_not_a_nodeid_yields_nothing(self, value):
        """A Postman item title must never be mistaken for a test identity."""
        assert nodeid_suffix(value) == ""


class TestAFailureIsAttributedToItsRequest:
    def test_the_record_carries_the_endpoint(self, middleware, tmp_path):
        req = _request("Get User By ID", "https://api.example.com/users/42")
        nodeid = _one(_nodeids([req], tmp_path / "gen"), "test_get_user_by_id")

        mw = middleware()
        mw.after_execution([_failure(nodeid)], [req], RUN_ID)

        record = mw._store.get_for_test(nodeid)[0]
        assert record.endpoint == "https://api.example.com/users/{id}"
        assert record.method == "GET"
        assert "admin" in record.payload_snippet

    def test_a_request_in_a_nested_folder_is_still_found(self, middleware, tmp_path):
        req = _request("Create Order", "https://api.example.com/orders", folder="Orders")
        req.folder_path = ["Shop", "Orders"]
        req.method = "POST"
        nodeid = _one(_nodeids([req], tmp_path / "gen"), "test_create_order")

        mw = middleware()
        mw.after_execution([_failure(nodeid)], [req], RUN_ID)

        assert mw._store.get_for_test(nodeid)[0].method == "POST"

    def test_each_failure_gets_its_own_request(self, middleware, tmp_path):
        users = _request("Get User By ID", "https://api.example.com/users/42")
        orders = _request("List Orders", "https://api.example.com/orders")
        nodeids = _nodeids([users, orders], tmp_path / "gen")
        user_test = _one(nodeids, "test_get_user_by_id")
        order_test = _one(nodeids, "test_list_orders")

        mw = middleware()
        mw.after_execution([_failure(user_test), _failure(order_test)], [users, orders], RUN_ID)

        endpoint = {n: mw._store.get_for_test(n)[0].endpoint for n in nodeids}
        assert endpoint[user_test] == "https://api.example.com/users/{id}"
        assert endpoint[order_test] == "https://api.example.com/orders"

    def test_active_mode_injects_the_history_it_stored(self, middleware, tmp_path):
        """
        The round trip: what ``after_execution`` writes, ``before_execution``
        must be able to find on the next run. Both halves keyed the lookup the
        same wrong way, so this never happened.
        """
        req = _request("Get User By ID", "https://api.example.com/users/42")
        nodeid = _one(_nodeids([req], tmp_path / "gen"), "test_get_user_by_id")

        mw = middleware(mode="active")
        mw.after_execution([_failure(nodeid)], [req], RUN_ID)

        ctx = ContextMemory()
        mw.before_execution([req], ctx)

        insights = ctx.get("memory_insights") or []
        assert len(insights) == 1
        assert "Get User By ID" in insights[0]


class TestAMissIsBetterThanAWrongMatch:
    """
    Negative controls.

    A record attributed to the wrong request is worse than one attributed to
    none: it teaches the memory layer that one test's failures belong to
    another test's endpoint, and there is nothing downstream to catch it. So
    the mapping must be exact, and a nodeid it does not recognise must produce
    an empty record rather than a plausible one.
    """

    def test_an_unknown_nodeid_attributes_nothing(self, middleware, tmp_path):
        req = _request("Get User By ID", "https://api.example.com/users/42")
        _nodeids([req], tmp_path / "gen")

        mw = middleware()
        nodeid = "tests/ui/test_login.py::test_login_page_loads"
        mw.after_execution([_failure(nodeid)], [req], RUN_ID)

        record = mw._store.get_for_test(nodeid)[0]
        assert record.endpoint == ""
        assert record.method == ""

    def test_a_same_named_request_in_another_folder_is_not_borrowed(self, middleware, tmp_path):
        """
        Two collections can both hold a "Get By ID". They generate into
        different files, so the file name is part of the identity.
        """
        users = _request("Get By ID", "https://api.example.com/users/42", folder="Users")
        orders = _request("Get By ID", "https://api.example.com/orders/42", folder="Orders")
        nodeids = _nodeids([users, orders], tmp_path / "gen")
        assert len(nodeids) == 2

        mw = middleware()
        for nodeid in nodeids:
            mw.after_execution([_failure(nodeid)], [users, orders], RUN_ID)

        endpoints = {mw._store.get_for_test(n)[0].endpoint for n in nodeids}
        assert endpoints == {
            "https://api.example.com/users/{id}",
            "https://api.example.com/orders/{id}",
        }

    def test_a_ui_test_does_not_pick_up_an_api_history(self, middleware, tmp_path):
        """``from_pytest_report`` writes UI runs into the same store."""
        req = _request("Get User By ID", "https://api.example.com/users/42")
        _nodeids([req], tmp_path / "gen")

        mw = middleware(mode="active")
        mw.after_execution(
            [_failure("ui/tests/test_search.py::test_get_user_by_id_widget")], [req], RUN_ID
        )

        ctx = ContextMemory()
        mw.before_execution([req], ctx)
        assert ctx.get("memory_insights") is None
