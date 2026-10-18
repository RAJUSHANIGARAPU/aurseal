"""Every browser test result carries its engine."""

from __future__ import annotations

from types import SimpleNamespace

from reporting.pytest_plugin import BROWSER_PROPERTY, tag_browser

pytest_plugins = ["pytester"]


def _item(nodeid, params=None):
    return SimpleNamespace(
        nodeid=nodeid, callspec=SimpleNamespace(params=params or {}), user_properties=[]
    )


def test_browser_test_gets_a_user_property_once():
    item = _item("ui/tests/t.py::test_search[webkit]")
    assert tag_browser(item) == "webkit"
    tag_browser(item)
    assert item.user_properties == [(BROWSER_PROPERTY, "webkit")]


def test_fixture_parameter_wins_over_the_node_id():
    item = _item("ui/tests/t.py::test_search[x]", {"browser_name": "firefox"})
    assert tag_browser(item) == "firefox"


def test_non_browser_test_is_untouched():
    item = _item("tests/t.py::test_unit")
    assert tag_browser(item) is None
    assert item.user_properties == []


def test_real_playwright_parametrisation_is_tagged(pytester):
    """pytest-playwright's own `browser_name` parameter reaches the result, no browser launched."""
    pytester.makeconftest(
        "from reporting.pytest_plugin import tag_browser\n"
        "def pytest_runtest_setup(item):\n"
        "    tag_browser(item)\n"
    )
    pytester.makepyfile(
        "def test_engine(browser_name, request):\n"
        "    assert ('browser', browser_name) in request.node.user_properties\n"
        "    assert browser_name == 'firefox'\n"
    )
    result = pytester.runpytest("--browser", "firefox", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*[[]chromium[]]*")
