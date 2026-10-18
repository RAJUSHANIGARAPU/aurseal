"""Teardown under a deadline: a hung close is abandoned, never waited on forever."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.bounded_close import (
    DEFAULT_TIMEOUT_S,
    aclose_within,
    close_within,
    configured_timeout_s,
)
from tests.core._log_capture import framework_log


class TestCloseWithin:
    def test_a_returning_close_is_awaited(self):
        done = []
        assert close_within("context", lambda: done.append(1), 1.0) is True
        assert done == [1]

    def test_a_hung_close_is_abandoned_at_the_deadline(self):
        release = threading.Event()
        started = time.monotonic()
        with framework_log() as log:
            assert close_within("browser", release.wait, 0.2) is False
        assert time.monotonic() - started < 2.0
        assert "Abandoning browser close" in log.text
        release.set()

    def test_the_abandoned_worker_is_a_daemon(self):
        """A non-daemon thread would move the hang from the start of the run to its exit."""
        release = threading.Event()
        close_within("browser", release.wait, 0.05)
        workers = [t for t in threading.enumerate() if t.name.startswith("bounded-close-")]
        assert workers and all(t.daemon for t in workers)
        release.set()

    def test_a_raising_close_is_logged_not_raised(self):
        def boom():
            raise RuntimeError("pipe closed")

        with framework_log() as log:
            assert close_within("context", boom, 1.0) is False
        assert "pipe closed" in log.text

    def test_none_is_a_successful_no_op(self):
        assert close_within("absent", None) is True


class TestAcloseWithin:
    def test_awaited_when_it_returns(self):
        async def close():
            return None

        assert asyncio.run(aclose_within("context", close, 1.0)) is True

    def test_cancelled_at_the_deadline(self):
        async def hang():
            await asyncio.sleep(10)

        assert asyncio.run(aclose_within("browser", hang, 0.05)) is False

    def test_errors_are_swallowed(self):
        async def boom():
            raise RuntimeError("gone")

        assert asyncio.run(aclose_within("browser", boom, 1.0)) is False


class TestTimeoutConfiguration:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", DEFAULT_TIMEOUT_S),
            ("2500", 2.5),
            ("junk", DEFAULT_TIMEOUT_S),
            ("-1", DEFAULT_TIMEOUT_S),
        ],
    )
    def test_values(self, raw, expected):
        assert configured_timeout_s({"PLAYWRIGHT_CLOSE_TIMEOUT_MS": raw}) == expected
