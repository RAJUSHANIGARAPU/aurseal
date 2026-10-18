"""The engine a test ran on (the ``browser`` user property) lands on its result row."""

from __future__ import annotations

from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]


def test_browser_user_property_is_recorded(pytester, store):
    pytester.makepyfile(
        "def test_a(record_property):\n"
        "    record_property('browser', 'firefox')\n"
        "def test_b():\n"
        "    pass\n"
    )
    pytester.runpytest(*PLUGIN).assert_outcomes(passed=2)
    rows = store.conn.execute("SELECT nodeid, browser FROM test_results ORDER BY nodeid").fetchall()
    assert [(r[0].split("::")[-1], r[1]) for r in rows] == [("test_a", "firefox"), ("test_b", None)]
