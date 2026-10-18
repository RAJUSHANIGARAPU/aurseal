"""Browser identity of node ids: extracted for results, stripped for test identity."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gate.browsers import SUPPORTED, browser_from_nodeid, item_browser, strip_browser_param


@pytest.mark.parametrize(
    "nodeid,stripped,browser",
    [
        ("tests/t.py::test_a", "tests/t.py::test_a", None),
        ("tests/t.py::test_a[firefox]", "tests/t.py::test_a", "firefox"),
        ("tests/t.py::test_a[a-chromium]", "tests/t.py::test_a[a]", "chromium"),
        ("tests/t.py::test_a[webkit-a-b]", "tests/t.py::test_a[a-b]", "webkit"),
        ("tests/t.py::Cls::test_a[1-2]", "tests/t.py::Cls::test_a[1-2]", None),
        ("tests/t.py::test_a[chromeish]", "tests/t.py::test_a[chromeish]", None),
    ],
)
def test_strip_and_extract(nodeid, stripped, browser):
    assert strip_browser_param(nodeid) == stripped
    assert browser_from_nodeid(nodeid) == browser


def test_every_engine_strips_to_one_identity():
    ids = {strip_browser_param(f"t.py::test_x[{b}]") for b in SUPPORTED}
    assert ids == {"t.py::test_x"}


def test_item_browser_prefers_the_fixture_parameter():
    item = SimpleNamespace(
        callspec=SimpleNamespace(params={"browser_name": "webkit"}), nodeid="t.py::x[1]"
    )
    assert item_browser(item) == "webkit"
    assert item_browser(SimpleNamespace(nodeid="t.py::x[firefox]")) == "firefox"
    assert item_browser(SimpleNamespace(nodeid="t.py::x")) is None
