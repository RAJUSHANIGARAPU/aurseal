"""
Which thread's traffic belongs to the running test.

``requests.Session.send`` is patched process-wide, but capture was armed on a
plain ``threading.local`` set by ``pytest_runtest_setup``. So a test that handed
work to a worker thread — ``orchestration/master_orchestrator.py`` runs plugins
on a ``ThreadPoolExecutor`` — issued its HTTP calls on a thread where the flag
had never been set. Those calls were dropped with no record and no log.

The tests below come in two halves and the second half is what keeps the fix
honest. "Worker traffic is captured" is satisfied trivially by arming capture
for the whole process, which files a stray thread's request under whichever test
happens to be running. So there are controls for a thread started before the
test, a thread outliving the test that started it, and traffic with no test
running at all — each must still be refused, and refused out loud.

Every thread here is synchronised with an ``Event`` and a ``join``; no test
depends on one thread getting there first.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from contract_testing import capture
from tests.contract_testing._fake_http import FakeHttp, fire

JOIN_TIMEOUT = 10


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


def _paths(interactions) -> list[str]:
    return [i["path"] for i in interactions]


class TestThreadsTheTestStarted:
    def test_a_worker_thread_is_captured(self, http):
        capture.activate(test_name="tests/t.py::test_a")
        done = threading.Event()

        def work():
            fire("/from-worker")
            done.set()

        worker = threading.Thread(target=work)
        worker.start()
        worker.join(JOIN_TIMEOUT)

        assert done.is_set(), "the worker never ran — the test proves nothing"
        assert http.sent == 1
        assert _paths(capture.deactivate()) == ["/from-worker"]

    def test_a_thread_pool_is_captured(self, http):
        """The orchestrator's shape: submit from the test thread, run elsewhere."""
        capture.activate(test_name="tests/t.py::test_pool")

        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in [pool.submit(fire, f"/plugin-{i}") for i in range(3)]:
                future.result(timeout=JOIN_TIMEOUT)

        assert sorted(_paths(capture.deactivate())) == ["/plugin-0", "/plugin-1", "/plugin-2"]

    def test_a_grandchild_thread_is_captured(self, http):
        """A worker that starts its own worker is still inside the test."""
        capture.activate(test_name="tests/t.py::test_nested")
        done = threading.Event()

        def grandchild():
            fire("/from-grandchild")
            done.set()

        def child():
            inner = threading.Thread(target=grandchild)
            inner.start()
            inner.join(JOIN_TIMEOUT)

        worker = threading.Thread(target=child)
        worker.start()
        worker.join(JOIN_TIMEOUT)

        assert done.is_set()
        assert _paths(capture.deactivate()) == ["/from-grandchild"]

    def test_is_active_is_true_inside_a_worker(self, http):
        capture.activate(test_name="tests/t.py::test_flag")
        seen: list[bool] = []

        worker = threading.Thread(target=lambda: seen.append(capture.is_active()))
        worker.start()
        worker.join(JOIN_TIMEOUT)

        assert seen == [True]


class TestTheMainThreadStillWorks:
    """
    Positive control. The threading fix must not be a rewrite that quietly
    breaks the only path that worked before.
    """

    def test_a_request_from_the_test_thread_is_captured(self, http):
        capture.activate(test_name="tests/t.py::test_main")
        fire("/from-main")
        assert _paths(capture.deactivate()) == ["/from-main"]

    def test_the_test_name_is_recorded(self, http):
        capture.activate(test_name="tests/t.py::test_named")
        fire("/from-main")
        assert capture.deactivate()[0]["test_name"] == "tests/t.py::test_named"

    def test_the_worker_carries_the_same_test_name(self, http):
        capture.activate(test_name="tests/t.py::test_named")
        worker = threading.Thread(target=fire, args=("/from-worker",))
        worker.start()
        worker.join(JOIN_TIMEOUT)
        assert capture.deactivate()[0]["test_name"] == "tests/t.py::test_named"


class TestThreadsThatBelongToNoTest:
    """
    Negative controls. Each of these passes on the broken code and must keep
    passing — they are what stops the fix from becoming "capture everything".
    """

    def test_nothing_is_captured_with_no_test_running(self, http):
        fire("/no-test")
        assert http.sent == 1, "the request did not happen — the control is vacuous"
        assert capture.deactivate() == []

    def test_nothing_is_captured_after_the_test_ended(self, http):
        capture.activate(test_name="tests/t.py::test_a")
        capture.deactivate()
        fire("/after-teardown")
        assert capture.deactivate() == []

    def test_a_thread_started_before_the_test_is_not_captured(self, http):
        """
        The thread is held until the test is active, so this is not a timing
        accident: the request lands squarely inside the test window and must
        still be refused, because the thread is not part of the test.
        """
        go = threading.Event()
        done = threading.Event()

        def stranger():
            go.wait(JOIN_TIMEOUT)
            fire("/from-stranger")
            done.set()

        worker = threading.Thread(target=stranger)
        worker.start()

        capture.activate(test_name="tests/t.py::test_b")
        go.set()
        worker.join(JOIN_TIMEOUT)

        assert done.is_set()
        assert http.sent == 1
        assert capture.deactivate() == []

    def test_a_thread_outliving_its_test_is_not_filed_under_the_next_one(self, http):
        """The bug a process-global flag would introduce, asserted directly."""
        capture.activate(test_name="tests/t.py::test_first")
        go = threading.Event()
        done = threading.Event()

        def straggler():
            go.wait(JOIN_TIMEOUT)
            fire("/late")
            done.set()

        worker = threading.Thread(target=straggler)
        worker.start()

        first = capture.deactivate()
        capture.activate(test_name="tests/t.py::test_second")
        go.set()
        worker.join(JOIN_TIMEOUT)
        second = capture.deactivate()

        assert done.is_set()
        assert first == []
        assert _paths(second) == [], "the first test's straggler was filed under the second test"


class TestARefusedRequestIsAnnounced:
    """
    A dropped request that says nothing is how this went unnoticed. Skipping is
    the right call; skipping silently is not.
    """

    def test_a_stray_thread_during_a_test_is_logged(self, http, caplog):
        go = threading.Event()
        done = threading.Event()

        def stranger():
            go.wait(JOIN_TIMEOUT)
            fire("/from-stranger")
            done.set()

        worker = threading.Thread(target=stranger)
        worker.start()

        capture.activate(test_name="tests/t.py::test_c")
        with caplog.at_level(logging.WARNING, logger="contract_testing.capture"):
            go.set()
            worker.join(JOIN_TIMEOUT)

        assert done.is_set()
        assert any("/from-stranger" in r.getMessage() for r in caplog.records)

    def test_the_log_line_does_not_carry_the_query_string(self, http, caplog):
        """
        Query strings carry api keys and presigned signatures — the whole point
        of ``redaction.py``. A diagnostic must not become the leak.
        """
        go = threading.Event()
        done = threading.Event()

        def stranger():
            go.wait(JOIN_TIMEOUT)
            fire("/reports?api_key=live_sk_9f3c2a77b41e")
            done.set()

        worker = threading.Thread(target=stranger)
        worker.start()

        capture.activate(test_name="tests/t.py::test_d")
        with caplog.at_level(logging.WARNING, logger="contract_testing.capture"):
            go.set()
            worker.join(JOIN_TIMEOUT)

        assert done.is_set()
        assert not any("live_sk_9f3c2a77b41e" in r.getMessage() for r in caplog.records)


class TestThePatchIsAlwaysRestored:
    def test_remove_restores_the_original_send(self, monkeypatch):
        fake = FakeHttp()
        monkeypatch.setattr(requests.Session, "send", fake.send, raising=True)
        original_send = requests.Session.send

        capture.install()
        assert requests.Session.send is not original_send
        capture.remove()
        assert requests.Session.send is original_send

    def test_remove_restores_thread_start(self, monkeypatch):
        fake = FakeHttp()
        monkeypatch.setattr(requests.Session, "send", fake.send, raising=True)
        original_start = threading.Thread.start

        capture.install()
        capture.remove()
        assert threading.Thread.start is original_start

    def test_a_send_that_raises_still_leaves_the_patch_removable(self, monkeypatch):
        def boom(self, request, **kwargs):  # noqa: ANN001, ARG001
            raise RuntimeError("connection reset")

        monkeypatch.setattr(requests.Session, "send", boom, raising=True)
        capture.install()
        capture.activate(test_name="tests/t.py::test_boom")
        try:
            with pytest.raises(RuntimeError):
                fire("/explodes")
        finally:
            capture.deactivate()
            capture.remove()
        assert requests.Session.send is boom
