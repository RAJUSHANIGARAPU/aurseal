"""
The browser-fixture template in ``ui/fixtures/browser_fixture.py``.

Nothing imports that module, so none of it runs — which is exactly why it could
carry a fatal bug indefinitely. ``--browser`` is an append-action option and so
a list, and it was handed straight to ``getattr``, which requires a string. The
fixture would have raised ``TypeError`` the first time anyone reached for the
template it exists to be.

Only the engine-name resolution is checked here. The fixtures themselves need a
real browser, and a template nobody has wired does not justify launching one in
the offline lane — but the part that was broken is pure and can be pinned.
"""

from __future__ import annotations

import pytest

from ui.fixtures.browser_fixture import _requested_browser


class _Config:
    """Stands in for pytest's config: `--browser` is action="append"."""

    def __init__(self, browser=None):
        self._browser = [] if browser is None else browser

    def getoption(self, name):
        assert name == "--browser"
        return self._browser


class TestTheResultIsUsableAsAnAttributeName:
    @pytest.mark.parametrize("requested", [None, ["chromium"], ["firefox"], ["webkit"]])
    def test_a_string_is_returned(self, requested):
        """`getattr` requires a string; a list raises TypeError."""
        assert isinstance(_requested_browser(_Config(requested)), str)

    def test_getattr_accepts_it(self):
        """
        The real failure, reproduced directly: the old code passed the list.
        Using a stand-in object rather than Playwright keeps this offline.
        """
        engines = type("Engines", (), {"chromium": "engine"})()
        assert getattr(engines, _requested_browser(_Config(["chromium"]))) == "engine"

    def test_the_old_shape_would_have_raised(self):
        """Pins why this function exists, so nobody 'simplifies' it back."""
        engines = type("Engines", (), {"chromium": "engine"})()
        with pytest.raises(TypeError):
            getattr(engines, _Config(["chromium"]).getoption("--browser"))


class TestWhichBrowserIsChosen:
    def test_the_requested_browser_is_used(self):
        assert _requested_browser(_Config(["firefox"])) == "firefox"

    def test_it_defaults_to_chromium_when_unset(self):
        assert _requested_browser(_Config()) == "chromium"

    def test_the_first_wins_when_several_are_given(self):
        """One fixture launches one browser; the matrix is pytest's job."""
        assert _requested_browser(_Config(["firefox", "webkit"])) == "firefox"
