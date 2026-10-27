"""``known_defect`` marker: expected-red tests that graduate when the defect is fixed.

    @pytest.mark.known_defect(
        "BUG-142", reason="rounding drops a cent", raises=AssertionError,
        match=r"split totals differ by \\d+ cent",
    )
    def test_split_totals_add_up(): ...

While the defect is open the test runs and is expected to fail; this is
``xfail(strict=True)`` underneath, so pytest's own reporting and xdist support
apply unchanged. What the marker adds over a bare strict xfail:

* a required defect reference, carried into the report and ``user_properties``;
* an unexpected pass fails with a message naming the defect to close;
* an external open/closed source: ids in ``KNOWN_DEFECTS_CLOSED`` (comma
  separated), or any ``is_open`` callable, turn the marker off so the test must
  pass as an ordinary test;
* ``browsers=("webkit",)``: scope the defect to some browser engines. On any
  other engine the marker does not apply and the test must pass as an ordinary
  test, so a matrix leg is never muted for a defect it does not have;
* ``match=``: a regex the failure message must contain for the failure to
  count as the known defect. A failure that does not match is reported as a
  plain failure, so an unrelated break cannot hide behind the marker. The
  pattern must say more than the defect id (see
  ``verification.defect_detector.require_usable_signature``);
* ``fix_line="2.4"``: the release line the fix ships in. With ``--release-line`` (or
  ``$RELEASE_LINE``) set to another line, the test is deselected from that run, so a fix
  the release does not contain cannot hold its gate; without it the test runs as usual,
  so it stays runnable on demand;
* one verdict per marked test in the run's verdict file
  (``verification.verdicts``): ``reproduces``, ``cleared``,
  ``signature_mismatch``, ``held_empty``, ``closed_pass`` or
  ``closed_regressed``.

Pass ``raises=`` and ``match=`` whenever possible: without them, a test that
fails for an unrelated reason (a typo, a broken fixture) is reported as the
known defect. A detector that needs a positive control uses
``verification.defect_detector.still_reproduces`` instead.

Registering also installs the end-of-run status report
(``verification.status_report``).

Registered from the root ``conftest.py``; elsewhere use ``-p verification.pytest_plugin``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable

import pytest

from gate.browsers import SUPPORTED as SUPPORTED_BROWSERS
from gate.browsers import item_browser
from verification.defect_detector import (
    CLOSED_ENV,
    closed_defects,
    require_defect_id,
    require_usable_signature,
)
from verification.status_report import VerdictStatusPlugin
from verification.verdicts import Kind, RedVerdict, record_verdict

MARKER = "known_defect"
RELEASE_LINE_OPTION = "--release-line"
RELEASE_LINE_ENV = "RELEASE_LINE"
PLUGIN_NAME = "known-defect"
STATUS_PLUGIN_NAME = "verdict-status"
__all__ = ["CLOSED_ENV", "MARKER", "KnownDefectPlugin", "register"]
_XPASS_PREFIX = "[XPASS(strict)]"


class KnownDefectPlugin:
    def __init__(self, is_open: Callable[[str], bool]) -> None:
        self._is_open = is_open
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._out_of_scope: set[str] = set()

    @classmethod
    def from_closed_ids(cls, closed: Iterable[str]) -> KnownDefectPlugin:
        closed_set = frozenset(c.strip() for c in closed if c.strip())
        return cls(lambda defect_id: defect_id not in closed_set)

    @classmethod
    def from_env(cls) -> KnownDefectPlugin:
        return cls.from_closed_ids(closed_defects())

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        _deselect_other_lines(config, items)
        for item in items:
            marker = item.get_closest_marker(MARKER)
            if marker is None:
                continue
            defect_id = marker.args[0] if marker.args else marker.kwargs.get("defect_id")
            if not isinstance(defect_id, str) or not defect_id.strip():
                raise pytest.UsageError(f"{item.nodeid}: @{MARKER} needs a defect id")
            try:
                require_defect_id(defect_id)
            except ValueError as exc:
                raise pytest.UsageError(f"{item.nodeid}: @{MARKER} {exc}") from exc
            if not _in_browser_scope(item, marker):
                self._out_of_scope.add(item.nodeid)
                continue
            match = marker.kwargs.get("match")
            if match is not None:
                try:
                    self._patterns[item.nodeid] = require_usable_signature(defect_id, match)
                except ValueError as exc:
                    raise pytest.UsageError(f"{item.nodeid}: @{MARKER} {exc}") from exc
            item.user_properties.append((MARKER, defect_id))
            if not self._is_open(defect_id):
                continue
            reason = marker.kwargs.get("reason", "")
            item.add_marker(
                pytest.mark.xfail(
                    strict=True,
                    raises=marker.kwargs.get("raises"),
                    reason=(
                        f"known defect {defect_id} is open{': ' + reason if reason else ''}. "
                        f"If this passed, {defect_id} looks fixed: verify, close it, "
                        f"and remove the marker"
                    ),
                )
            )

    # Outermost wrapper, so it sees the report after pytest's xfail handling.
    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo[None]):
        report = yield
        marker = item.get_closest_marker(MARKER)
        if marker is None or call.when == "teardown" or item.nodeid in self._out_of_scope:
            return report
        if call.when == "setup":
            if not report.passed:
                self._record(item, RedVerdict.HELD_EMPTY, f"setup {report.outcome}")
            return report
        defect_id = dict(item.user_properties).get(MARKER, "")
        verdict = self._judge(item, call, report, self._is_open(defect_id))
        self._record(item, verdict, _message(call, report))
        return report

    def _judge(
        self, item: pytest.Item, call: pytest.CallInfo[None], report: pytest.TestReport, open_: bool
    ) -> RedVerdict:
        documented = self._documented(item, call)
        if not open_:
            if report.passed:
                return RedVerdict.CLOSED_PASS
            if report.skipped:
                return RedVerdict.HELD_EMPTY
            return RedVerdict.CLOSED_REGRESSED if documented else RedVerdict.SIGNATURE_MISMATCH
        if report.skipped and hasattr(report, "wasxfail"):
            if documented:
                return RedVerdict.REPRODUCES
            # xfail accepted it; the signature did not. Report it as the failure it is.
            del report.wasxfail
            report.outcome = "failed"
            pattern = self._patterns[item.nodeid].pattern
            report.longrepr = (
                f"{item.nodeid}: failed for an UNDOCUMENTED reason, not the known defect. "
                f"Expected the failure to match /{pattern}/, got: {_message(call, report)}"
            )
            return RedVerdict.SIGNATURE_MISMATCH
        if report.skipped:
            return RedVerdict.HELD_EMPTY
        if report.failed and str(report.longrepr).startswith(_XPASS_PREFIX):
            return RedVerdict.CLEARED
        return RedVerdict.SIGNATURE_MISMATCH

    def _documented(self, item: pytest.Item, call: pytest.CallInfo[None]) -> bool:
        if call.excinfo is None:
            return False
        marker = item.get_closest_marker(MARKER)
        raises = marker.kwargs.get("raises") if marker is not None else None
        if raises is not None and not call.excinfo.errisinstance(raises):
            return False
        pattern = self._patterns.get(item.nodeid)
        return pattern is None or bool(pattern.search(str(call.excinfo.value)))

    @staticmethod
    def _record(item: pytest.Item, verdict: RedVerdict, detail: str) -> None:
        defect_id = dict(item.user_properties).get(MARKER)
        record_verdict(
            Kind.EXPECTED_RED, verdict, ticket=defect_id, detail=detail, nodeid=item.nodeid
        )


def release_line(config: pytest.Config) -> str | None:
    """The release line this run gates: ``--release-line``, else ``$RELEASE_LINE``."""
    try:
        chosen = config.getoption(RELEASE_LINE_OPTION)
    except ValueError:  # the option is registered by the root conftest only
        chosen = None
    chosen = chosen or os.environ.get(RELEASE_LINE_ENV, "")
    return chosen.strip() or None


def _deselect_other_lines(config: pytest.Config, items: list[pytest.Item]) -> None:
    line = release_line(config)
    if line is None:
        return
    kept, dropped = [], []
    for item in items:
        marker = item.get_closest_marker(MARKER)
        fix_line = marker.kwargs.get("fix_line") if marker is not None else None
        (dropped if fix_line is not None and str(fix_line) != line else kept).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


def _in_browser_scope(item: pytest.Item, marker: pytest.Mark) -> bool:
    """False when the marker is scoped to engines this item does not run on."""
    scope = marker.kwargs.get("browsers")
    if scope is None:
        return True
    scope = (scope,) if isinstance(scope, str) else tuple(scope)
    unknown = sorted(set(scope) - set(SUPPORTED_BROWSERS))
    if not scope or unknown:
        raise pytest.UsageError(
            f"{item.nodeid}: @{MARKER} browsers={scope!r}: use some of {SUPPORTED_BROWSERS}"
        )
    browser = item_browser(item)
    # An item with no engine cannot be scoped out; the defect applies as declared.
    return browser is None or browser in scope


def _message(call: pytest.CallInfo[None], report: pytest.TestReport) -> str:
    if call.excinfo is not None:
        return str(call.excinfo.value)
    return str(getattr(report, "wasxfail", "") or report.longrepr or "")


def register(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}(defect_id, reason='', raises=None, match=None, browsers=None, "
        f"fix_line=None): expected "
        f"to fail while the defect is open; an unexpected pass fails the run "
        f"({CLOSED_ENV} lists fixed ids)",
    )
    if not config.pluginmanager.hasplugin(PLUGIN_NAME):
        config.pluginmanager.register(KnownDefectPlugin.from_env(), PLUGIN_NAME)
    if not config.pluginmanager.hasplugin(STATUS_PLUGIN_NAME):
        config.pluginmanager.register(VerdictStatusPlugin(config), STATUS_PLUGIN_NAME)


def pytest_configure(config: pytest.Config) -> None:
    register(config)
