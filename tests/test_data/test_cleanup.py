"""Tests for test_data.cleanup, test_data.drafts and the cleanup_ledger fixture."""

from __future__ import annotations

import pytest

from api.clients.http_engine import ApiError, ApiResponse, HttpEngine
from test_data.cleanup import CleanupLedger, CreationRule, cancel_pending_jobs
from test_data.drafts import open_draft
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

pytest_plugins = ["pytester"]


def _json(status: int, body: str) -> ApiResponse:
    return ApiResponse(status, body.encode())


class TestLedger:
    def test_discards_newest_first_and_forgets(self):
        ledger, order = CleanupLedger(), []
        ledger.record("a", "1", lambda: order.append("a1"))
        ledger.record("b", "2", lambda: order.append("b2"))
        report = ledger.discard_all()
        assert order == ["b2", "a1"] and report.discarded == ["b:2", "a:1"]
        assert ledger.entries == [] and ledger.discard_all().discarded == []

    def test_duplicates_are_recorded_once_and_forget_removes(self):
        ledger = CleanupLedger()
        ledger.record("a", "1", lambda: None)
        ledger.record("a", "1", lambda: None)
        ledger.record("a", "2", lambda: None)
        ledger.forget("a", "2")
        assert [e.ident for e in ledger.entries] == ["1"]

    def test_a_failing_discard_never_raises_and_the_rest_still_run(self):
        ledger, ran = CleanupLedger(), []
        ledger.record("a", "1", lambda: ran.append(1))
        ledger.record("b", "2", lambda: 1 / 0)
        ledger.record("c", "3", lambda: ApiResponse(500))
        ledger.record("d", "4", lambda: ApiResponse(404))  # already gone counts as discarded
        report = ledger.discard_all()
        assert ran == [1]
        assert report.failed == ["c:3", "b:2"] and not report.clean
        assert "d:4" in report.discarded

    def test_observer_records_successful_creates_only(self):
        deleted = []
        rule = CreationRule("order", "POST", r"/orders$", deleted.append, id_path="data.id")
        loc_rule = CreationRule("file", "POST", r"/files$", deleted.append, id_path=None)
        ledger = CleanupLedger([rule, loc_rule])
        ledger.observer("POST", "https://h/orders", _json(201, '{"data": {"id": "o-1"}}'))
        ledger.observer("POST", "https://h/orders", _json(400, '{"data": {"id": "o-2"}}'))
        ledger.observer("GET", "https://h/orders", _json(200, '{"data": {"id": "o-3"}}'))
        ledger.observer("POST", "https://h/orders", _json(201, "{}"))  # id unreadable
        ledger.observer("POST", "https://h/files", ApiResponse(201, headers={"Location": "/f/9"}))
        ledger.discard_all()
        assert deleted == ["9", "o-1"]

    def test_wired_to_the_engine_every_create_is_cleaned(self):
        adapter = (
            FakeAdapter()
            .on("POST", "/orders", make_response(201, {"id": "o-7"}))
            .on("DELETE", "/orders/o-7", make_response(204))
        )
        engine = HttpEngine("https://h/", session=session_with(adapter))
        ledger = CleanupLedger(
            [CreationRule("order", "POST", r"/orders$", lambda i: engine.delete(f"orders/{i}"))]
        )
        engine.observe(ledger.observer)
        engine.post("orders", json={})
        assert ledger.discard_all().discarded == ["order:o-7"]
        assert adapter.sent[-1].method == "DELETE"


class TestJobCancellation:
    JOBS = (
        '{"items": [{"id": "j1", "state": "SCHEDULED", "ref": "t-1"},'
        ' {"id": "j2", "state": "RUNNING", "ref": "t-1"},'
        ' {"id": "j3", "state": "SCHEDULED", "ref": "other"},'
        ' {"id": "j4", "state": "RETRY_PENDING", "ref": "t-1"}]}'
    )

    def test_cancels_only_this_tests_pending_jobs(self):
        cancelled = []
        out = cancel_pending_jobs(
            lambda: _json(200, self.JOBS),
            lambda i: cancelled.append(i) or ApiResponse(204),
            belongs_to_test=lambda job: job["ref"] == "t-1",
            jobs_path="items",
        )
        assert cancelled == ["j1", "j4"] and out.skipped == ["j2"] and out.readable

    @pytest.mark.parametrize("status", [401, 403, 500])
    def test_unreadable_listing_is_reported_once(self, status):
        out = cancel_pending_jobs(
            lambda: ApiResponse(status), lambda i: 1 / 0, belongs_to_test=lambda j: True
        )
        assert not out.readable and out.cancelled == []

    def test_failed_cancel_is_reported(self):
        out = cancel_pending_jobs(
            lambda: _json(200, '[{"id": "j1", "state": "PENDING"}]'),
            lambda i: ApiResponse(500),
            belongs_to_test=lambda j: True,
        )
        assert out.failed == ["j1"]


class TestOpenDraft:
    def test_conflict_discards_stale_then_records_new_draft(self):
        calls = iter([ApiResponse(409), _json(201, '{"id": "d-2"}')])
        stale, discarded = [], []
        ledger = CleanupLedger()
        ident = open_draft(
            lambda: next(calls),
            draft_id=lambda r: r.json()["id"],
            discard=discarded.append,
            discard_stale=lambda: stale.append(True),
            ledger=ledger,
        )
        assert ident == "d-2" and stale == [True]
        ledger.discard_all()
        assert discarded == ["d-2"]

    def test_conflict_without_recovery_raises(self):
        with pytest.raises(ApiError):
            open_draft(lambda: ApiResponse(409), draft_id=str, discard=print)


def test_fixture_discards_even_when_the_test_fails(pytester):
    pytester.makepyfile(
        """
        DISCARDED = []

        def test_fails(cleanup_ledger):
            cleanup_ledger.record("thing", "t1", lambda: DISCARDED.append("t1"))
            assert False

        def test_cleanup_ran():
            assert DISCARDED == ["t1"]

        def test_failed_cleanup_is_reported(cleanup_ledger, request):
            cleanup_ledger.record("thing", "t2", lambda: 1 / 0)
        """
    )
    result = pytester.runpytest("-p", "test_data.pytest_plugin", "-rA")
    result.assert_outcomes(passed=2, failed=1)
