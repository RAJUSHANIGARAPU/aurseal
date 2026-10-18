"""Every chromium launch carries the switch that keeps Chrome's updater off the close path.

Without it, a branded-Chrome ``browser.close()`` waits for GoogleUpdater to exit:
110-750s per xdist worker in the demo_ui lane, all tests green.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import checks._browser as checks_browser
from core.chrome_launch import CHROME_ARGS, with_chrome_args

ROOT = Path(__file__).resolve().parents[2]


def test_the_updater_switch_is_present():
    assert "--disable-updater-scheduler" in CHROME_ARGS


def test_merge_keeps_caller_args_first_and_adds_each_switch_once():
    merged = with_chrome_args(["--no-sandbox", "--disable-updater-scheduler"])
    assert merged == ["--no-sandbox", "--disable-updater-scheduler"]
    assert with_chrome_args() == list(CHROME_ARGS)


def test_the_pytest_playwright_launch_args_carry_the_switch():
    """The root conftest feeds pytest-playwright's ``browser`` fixture; call its body directly."""
    source = (ROOT / "conftest.py").read_text()
    namespace: dict[str, object] = {}
    start = source.index("def browser_type_launch_args(")
    end = source.index("\n\n\n", start)
    exec(compile(source[start:end], "conftest.py", "exec"), namespace)  # noqa: S102
    launch_args = namespace["browser_type_launch_args"]
    args = launch_args({"headless": True}, "chromium")["args"]
    assert set(CHROME_ARGS) <= set(args)
    assert "--no-sandbox" in args
    # WebKit refuses unknown switches, so it gets the base options untouched.
    assert launch_args({"headless": True}, "webkit") == {"headless": True}


class _Recorder:
    def __init__(self) -> None:
        self.launches: list[dict[str, object]] = []

    def launch(self, **options: object) -> object:
        self.launches.append(options)
        browser = types.SimpleNamespace(close=lambda: None)
        browser.new_context = lambda **_: types.SimpleNamespace()
        return browser


@pytest.mark.parametrize(
    ("engine", "channel", "expected"),
    [
        ("chromium", "chrome", {"channel": "chrome", "args": list(CHROME_ARGS)}),
        ("chromium", None, {"args": list(CHROME_ARGS)}),
        ("firefox", None, {}),
    ],
)
def test_the_checks_browser_launch_carries_the_switch_for_chromium_only(
    monkeypatch, engine, channel, expected
):
    recorder = _Recorder()
    pw = types.SimpleNamespace(chromium=recorder, firefox=recorder, stop=lambda: None)
    manager = types.SimpleNamespace(start=lambda: pw)
    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: manager
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    monkeypatch.delenv(checks_browser.CHANNEL_ENV, raising=False)

    with checks_browser.browser_context(engine, channel=channel):
        pass

    assert recorder.launches == [expected]
