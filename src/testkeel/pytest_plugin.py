"""pytest plugin: write the run as a canonical ``RunUpload`` JSON document.

Off unless ``--testkeel-out=PATH`` is given; without it the plugin registers its option and
a marker and does nothing else. Disable it entirely with ``-p no:testkeel``.

Identity comes from the node id, not from a reconstructed JUnit class name:
``tests/shop/test_cart.py::TestCart::test_total[EUR-10]`` is path
``tests/shop/test_cart.py``, suite ``TestCart``, name ``test_total``, variant ``EUR-10``.
An explicit case id comes from ``@pytest.mark.case("SHOP-7")`` (or ``case_id``), a
``record_property("case_id", ...)`` call, or a title token (``test_pay[SHOP-7]`` is a
variant, not an id: use the marker).

Status, per attempt (one setup/call/teardown pass):

* setup or teardown error -> broken; call failure -> failed (as the JUnit importer maps
  ``<error>`` and ``<failure>``);
* skip -> skipped; ``xfail`` -> skipped with reason ``xfail``;
* ``xpass`` -> passed with reason ``xpass``; a strict ``xpass`` fails the test -> failed
  with reason ``xpass_strict``;
* pytest-rerunfailures logs every failed attempt with outcome ``rerun``: each one closes
  an attempt, so a test that passed on its second run is one passed record with
  ``flaky_in_run``.

Duration is setup + call + teardown of the attempt. Under pytest-xdist the controller
writes the file from the reports the workers send; the workers write nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from testkeel.importers._build import PendingTest, seconds_to_ms
from testkeel.model.identity import split_variant
from testkeel.model.v1 import RunUpload, Status

OPTION = "--testkeel-out"
CASE_MARKERS = ("case", "case_id")
CASE_PROPERTY = "case_id"
_XPASS_STRICT = "[XPASS(strict)]"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("testkeel")
    group.addoption(
        OPTION,
        dest="testkeel_out",
        metavar="PATH",
        default=None,
        help="Write the results as a testkeel RunUpload JSON document to PATH.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "case(id): the explicit test case id, e.g. PAY-123")
    out = config.getoption("testkeel_out")
    if not out:
        return
    # Workers collect, so they pin the case ids; only the controller writes the file.
    config.pluginmanager.register(_CaseIds(), "testkeel-case-ids")
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(Recorder(Path(out)), "testkeel-recorder")


class _CaseIds:
    """Copies a ``case`` marker into ``user_properties``, which travel with every report."""

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            if any(name == CASE_PROPERTY for name, _ in item.user_properties):
                continue
            for marker_name in CASE_MARKERS:
                marker = item.get_closest_marker(marker_name)
                if marker is not None and marker.args:
                    item.user_properties.append((CASE_PROPERTY, str(marker.args[0])))
                    break


def split_nodeid(nodeid: str) -> tuple[str, list[str], str, str | None]:
    """``(path, suite levels, name, variant)`` of a pytest node id."""
    base, variant = split_variant(nodeid)
    path, *rest = base.split("::")
    if not rest:  # a non-Python item (doctest file, plugin item): the file is the test
        return path, [], path.rsplit("/", 1)[-1], variant
    return path, rest[:-1], rest[-1], variant


def _message(report: pytest.TestReport) -> str | None:
    if hasattr(report, "wasxfail"):
        return str(report.wasxfail) or None
    longrepr = report.longrepr
    if longrepr is None:
        return None
    if isinstance(longrepr, tuple):  # skip: (path, line, "Skipped: reason")
        text = str(longrepr[2])
        return text.removeprefix("Skipped: ") or None
    crash = getattr(longrepr, "reprcrash", None)
    if crash is not None and getattr(crash, "message", None):
        return str(crash.message)
    text = str(longrepr).strip()
    return text.splitlines()[0] if text else None


@dataclass
class _Attempt:
    status: Status = Status.PASSED
    reason: str | None = None
    message: str | None = None
    duration: float = 0.0
    decided: bool = False

    def set(self, status: Status, message: str | None, reason: str | None = None) -> None:
        if not self.decided:
            self.status, self.message, self.reason, self.decided = status, message, reason, True


@dataclass
class _Test:
    pending: PendingTest
    current: _Attempt = field(default_factory=_Attempt)
    open: bool = False


class Recorder:
    """Collects reports per node id and writes the upload at session end."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.started_at = datetime.now(timezone.utc)
        self.tests: dict[str, _Test] = {}

    def _test(self, report: pytest.TestReport) -> _Test:
        test = self.tests.get(report.nodeid)
        if test is None:
            path, suite, name, variant = split_nodeid(report.nodeid)
            pending = PendingTest(
                framework="pytest", path=path, suite=suite, title=name, variant=variant
            )
            test = self.tests[report.nodeid] = _Test(pending)
        if test.pending.case_id is None:  # record_property() lands on the call report
            test.pending.case_id = next(
                (str(v) for k, v in report.user_properties if k == CASE_PROPERTY and v), None
            )
        return test

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        test = self._test(report)
        attempt = test.current
        test.open = True
        attempt.duration += report.duration or 0.0
        outcome: str = report.outcome  # pytest-rerunfailures adds "rerun"
        message = _message(report)
        failed = outcome in ("failed", "rerun")
        if report.when == "call":
            if hasattr(report, "wasxfail"):
                if outcome == "skipped":
                    attempt.set(Status.SKIPPED, message, "xfail")
                else:
                    attempt.set(Status.PASSED, message, "xpass")
            elif failed:
                text = str(report.longrepr or "")
                reason = "xpass_strict" if text.startswith(_XPASS_STRICT) else None
                attempt.set(Status.FAILED, message, reason)
            elif outcome == "skipped":
                attempt.set(Status.SKIPPED, message)
            else:
                attempt.set(Status.PASSED, None)
        elif failed:
            if attempt.decided and attempt.status is Status.FAILED:
                pass  # teardown error after a failed call: the call failure is the result
            else:
                attempt.decided = False
                attempt.set(Status.BROKEN, message)
        elif outcome == "skipped":  # skipped in setup (skip marker, skipif, xfail(run=False))
            reason = "xfail" if hasattr(report, "wasxfail") else None
            attempt.set(Status.SKIPPED, message, reason)
        if outcome == "rerun" or report.when == "teardown":
            self._close(test)

    def _close(self, test: _Test) -> None:
        attempt = test.current
        test.pending.add(
            attempt.status, seconds_to_ms(attempt.duration), attempt.message, attempt.reason
        )
        test.current = _Attempt()
        test.open = False

    def upload(self) -> RunUpload:
        for test in self.tests.values():
            if test.open:  # interrupted before teardown
                self._close(test)
        records = tuple(test.pending.record() for test in self.tests.values())
        return RunUpload(started_at=self.started_at, records=records)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text(self.upload().model_dump_json(indent=2) + "\n", encoding="utf-8")

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        terminalreporter.write_sep("-", f"testkeel upload: {self.out}")


__all__ = ["OPTION", "Recorder", "split_nodeid"]
