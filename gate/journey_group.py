"""Journey-group fail-fast: one broken shared step should not cost the whole run.

If a step shared by many page objects breaks, all tests going through that flow
fail in the same place, and a serial end-to-end run burns its entire time budget
repeating a single failure. Tests opt in with
``@pytest.mark.journey_group("group")``; on the first *deterministic* failure in
a group the remaining peers are skipped with a reason naming the break point,
and the first failure keeps its full diagnostics (and its reruns).

* Fingerprint: the deepest traceback frame whose file lies under one of the
  page-object paths (``FAILFAST_PAGE_PATHS``, default ``ui/pages/``), as
  ``file.py:function:line``. A failure with no such frame — a plain assertion
  in the test body — is not a shared-step break and never gates the group.
* Timeouts do not gate. Against a shared, non-isolated environment a timeout
  is the signature of load, not of a broken step, and it lands at a different
  step each run. It is logged and peers stay runnable
  (``JOURNEY_GROUP_TOLERATE_TIMEOUTS=false`` restores gating on timeouts).
* Upstream stalls do not gate either, under the same switch. The upstream-timeout count
  (``gate.circuit_breaker.upstream_timeouts``) is sampled when each test starts; a test
  that fails after the count rose failed while a third party behind the environment was
  timing out, so its peers keep running.
* Only a *final* failure counts: an attempt pytest-rerunfailures will retry
  does not gate anything.
* Recording is always on; skipping only when ``FAILFAST_JOURNEY_GROUP`` is true,
  or ``CI`` is set and ``FAILFAST_JOURNEY_GROUP`` is not. Local runs run everything.

Under xdist, state is per worker. Every grouped test also gets
``xdist_group("journey_group:<group>")``, so with ``--dist loadgroup`` a group
runs on one worker and fail-fast holds across it.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import PurePath

import pytest

from core.logger import logger
from gate import circuit_breaker
from gate._env import env_bool, env_list

MARKER = "journey_group"
ENABLED_ENV = "FAILFAST_JOURNEY_GROUP"
EXEMPT_TIMEOUTS_ENV = "JOURNEY_GROUP_TOLERATE_TIMEOUTS"
PAGE_PATHS_ENV = "FAILFAST_PAGE_PATHS"
DEFAULT_PAGE_PATHS = ("ui/pages/",)


def skip_enabled() -> bool:
    if os.environ.get(ENABLED_ENV, "").strip():
        return env_bool(ENABLED_ENV, False)
    ci = os.environ.get("CI", "").strip().lower()
    return bool(ci) and ci not in ("false", "0", "no")


def _chain(exc: BaseException | None):
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def fingerprint(exc: BaseException, page_paths: tuple[str, ...] = DEFAULT_PAGE_PATHS) -> str | None:
    """``file.py:function:line`` of the deepest page-object frame in the exception chain."""
    for link in _chain(exc):
        for frame in reversed(traceback.extract_tb(link.__traceback__)):
            filename = PurePath(frame.filename).as_posix()
            if any(p in filename for p in page_paths):
                return f"{PurePath(filename).name}:{frame.name}:{frame.lineno}"
    return None


def is_timeout(exc: BaseException) -> bool:
    for link in _chain(exc):
        name = type(link).__name__
        if name.endswith(("TimeoutError", "TimeoutException")) or name == "TimeoutExpired":
            return True
        message = str(link)
        if "Timeout" in message and "exceeded" in message:
            return True
    return False


@dataclass(frozen=True)
class FlowBreak:
    fingerprint: str
    nodeid: str


class JourneyGroupPlugin:
    def __init__(self, page_paths: tuple[str, ...] | None = None) -> None:
        self.page_paths = page_paths or env_list(PAGE_PATHS_ENV, DEFAULT_PAGE_PATHS)
        self.failures: dict[str, FlowBreak] = {}
        self._pending: dict[str, tuple[str, str]] = {}
        self._upstream_at_start: dict[str, int] = {}

    @staticmethod
    def group_of(item: pytest.Item) -> str | None:
        marker = item.get_closest_marker(MARKER)
        if marker is None:
            return None
        group = marker.args[0] if marker.args else marker.kwargs.get("group")
        return group.strip() if isinstance(group, str) and group.strip() else None

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            group = self.group_of(item)
            if group is not None:
                item.add_marker(pytest.mark.xdist_group(name=f"{MARKER}:{group}"))

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        group = self.group_of(item)
        if group is None:
            return
        self._upstream_at_start[item.nodeid] = circuit_breaker.upstream_timeouts()
        if not skip_enabled():
            return
        failure = self.failures.get(group)
        if failure is None or failure.nodeid == item.nodeid:
            return
        reason = (
            f"journey group {group!r} already failed at {failure.fingerprint} "
            f"(first seen in {failure.nodeid}) - skipped to fail fast"
        )
        logger.warning("[failfast] skipping %s - %s", item.nodeid, reason)
        pytest.skip(reason)

    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo) -> None:
        if call.when != "call" or call.excinfo is None:
            return
        group = self.group_of(item)
        if group is None or call.excinfo.errisinstance(pytest.skip.Exception):
            return
        exc = call.excinfo.value
        found = fingerprint(exc, self.page_paths)
        if found is None:
            return
        tolerate = env_bool(EXEMPT_TIMEOUTS_ENV, True)
        if tolerate and is_timeout(exc):
            logger.warning(
                "[failfast] journey group %r timed out at %s in %s - peers keep running",
                group,
                found,
                item.nodeid,
            )
            return
        started = self._upstream_at_start.get(item.nodeid)
        if tolerate and started is not None and circuit_breaker.upstream_timeouts() > started:
            logger.warning(
                "[failfast] %s failed after an upstream timeout (%s), peers keep running",
                item.nodeid,
                circuit_breaker.last_upstream_timeout(),
            )
            return
        self._pending[item.nodeid] = (group, found)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        pending = self._pending.pop(report.nodeid, None) if report.when == "call" else None
        # A "rerun" outcome is an attempt that will be retried, not a failure of the test.
        if pending is None or report.outcome != "failed":
            return
        group, found = pending
        if group not in self.failures:
            self.failures[group] = FlowBreak(found, report.nodeid)
            logger.warning(
                "[failfast] journey group %r broke at %s (first seen in %s) - peers %s",
                group,
                found,
                report.nodeid,
                "will be skipped" if skip_enabled() else "would be skipped (skipping disabled)",
            )
