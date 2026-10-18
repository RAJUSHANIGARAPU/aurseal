"""The ``faults`` fixture: inject, record, clean up, and refuse a pass nothing exercised.

Load with ``-p checks.faults.pytest_plugin``, or import ``faults`` and
``pytest_runtest_makereport`` into a ``conftest.py`` (hooks in a conftest apply to the tests
under it). Registers the ``chaos`` marker.

* ``faults.browser(context_or_page, specs, seed=0)`` / ``faults.api(session, specs, seed=0)``
  install faults and return the handle;
* at teardown every handle is removed and one ``metric_fault_injection`` row per spec is
  written (``METRICS_DIR``);
* a test that passed while **no** installed fault fired is reported as skipped
  (``held_empty``): a resilience test whose fault never happened proved nothing. This is
  the positive control, applied to every test that uses the fixture.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import pytest

from checks._sink import JsonlSink, MetricSink, metric_row
from checks.faults.api import FaultyTransport
from checks.faults.browser import FaultHandle, inject
from checks.faults.spec import FaultSpec

TABLE = "metric_fault_injection"
HELD_EMPTY = "held_empty"


class FaultController:
    def __init__(self, nodeid: str, sink: MetricSink | None) -> None:
        self.nodeid = nodeid
        self.sink = sink
        self.handles: list[FaultHandle | FaultyTransport] = []
        self.oracles: list[tuple[str, str]] = []

    def browser(self, target: Any, specs: Sequence[FaultSpec], *, seed: int = 0) -> FaultHandle:
        handle = inject(target, specs, seed=seed)
        self.handles.append(handle)
        return handle

    def api(self, session: Any, specs: Sequence[FaultSpec], *, seed: int = 0) -> FaultyTransport:
        transport = FaultyTransport(session, specs, seed=seed)
        self.handles.append(transport)
        return transport

    def note(self, oracle_result: Any) -> Any:
        """Record an oracle's outcome next to the faults in the metric rows."""
        self.oracles.append(("pass" if oracle_result.passed else "fail", oracle_result.name))
        return oracle_result

    def fired(self) -> int:
        return sum(h.plan.fired() for h in self.handles)

    def close(self) -> None:
        rows = []
        oracle = ",".join(name for _, name in self.oracles) or None
        oracle_outcome = None
        if self.oracles:
            oracle_outcome = "fail" if any(o == "fail" for o, _ in self.oracles) else "pass"
        for handle in self.handles:
            handle.remove()
            for spec in handle.plan.specs:
                rows.append(
                    metric_row(
                        nodeid=self.nodeid,
                        spec_id=spec.id,
                        kind=spec.kind.value,
                        url_glob=spec.url_glob,
                        fired=handle.plan.fired(spec.id),
                        oracle=oracle,
                        oracle_outcome=oracle_outcome,
                    )
                )
        if self.sink is not None and rows:
            self.sink.write(TABLE, rows)


@pytest.fixture
def faults(request: pytest.FixtureRequest) -> Iterator[FaultController]:
    controller = FaultController(request.node.nodeid, JsonlSink())
    request.node.stash[_KEY] = controller
    try:
        yield controller
    finally:
        controller.close()


_KEY = pytest.StashKey[FaultController]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[None]:
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.passed:
        return
    controller = item.stash.get(_KEY, None)
    if controller is None or not controller.handles or controller.fired() > 0:
        return
    report.outcome = "skipped"
    report.longrepr = (
        str(item.path),
        item.location[1] or 0,
        f"Skipped: {HELD_EMPTY}: no injected fault fired, so the test proved nothing",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "chaos: fault-injection (resilience) tests")
