"""``known_defect`` marker: expected-red tests that graduate when the defect is fixed.

    @pytest.mark.known_defect("BUG-142", reason="rounding drops a cent", raises=AssertionError)
    def test_split_totals_add_up(): ...

While the defect is open the test runs and is expected to fail; this is
``xfail(strict=True)`` underneath, so pytest's own reporting and xdist support
apply unchanged. What the marker adds over a bare strict xfail:

* a required defect reference, carried into the report and ``user_properties``;
* an unexpected pass fails with a message naming the defect to close;
* an external open/closed source: ids in ``KNOWN_DEFECTS_CLOSED`` (comma
  separated), or any ``is_open`` callable, turn the marker off so the test must
  pass as an ordinary test.

Pass ``raises=`` whenever possible: without it, a test that fails for an
unrelated reason (a typo, a broken fixture) is reported as the known defect.

Registered from the root ``conftest.py``; elsewhere use ``-p verification.pytest_plugin``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable

import pytest

MARKER = "known_defect"
PLUGIN_NAME = "known-defect"
CLOSED_ENV = "KNOWN_DEFECTS_CLOSED"


class KnownDefectPlugin:
    def __init__(self, is_open: Callable[[str], bool]) -> None:
        self._is_open = is_open

    @classmethod
    def from_closed_ids(cls, closed: Iterable[str]) -> KnownDefectPlugin:
        closed_set = frozenset(c.strip() for c in closed if c.strip())
        return cls(lambda defect_id: defect_id not in closed_set)

    @classmethod
    def from_env(cls) -> KnownDefectPlugin:
        return cls.from_closed_ids(os.environ.get(CLOSED_ENV, "").split(","))

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            marker = item.get_closest_marker(MARKER)
            if marker is None:
                continue
            defect_id = marker.args[0] if marker.args else marker.kwargs.get("defect_id")
            if not isinstance(defect_id, str) or not defect_id.strip():
                raise pytest.UsageError(f"{item.nodeid}: @{MARKER} needs a defect id")
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


def register(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}(defect_id, reason='', raises=None): expected to fail while the "
        f"defect is open; an unexpected pass fails the run ({CLOSED_ENV} lists fixed ids)",
    )
    if not config.pluginmanager.hasplugin(PLUGIN_NAME):
        config.pluginmanager.register(KnownDefectPlugin.from_env(), PLUGIN_NAME)


def pytest_configure(config: pytest.Config) -> None:
    register(config)
