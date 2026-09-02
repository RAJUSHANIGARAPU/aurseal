"""
Link enqueueing in CrawlEngine.

``engine.py`` sits at 0% coverage because ``crawl()`` needs a live browser. But
``_enqueue_links`` — the method that decides what the crawler visits next, and
the one changed to stop losing links — only touches ``page.evaluate`` and
``page.url``, so it runs against a stub.

The defect it covers: the href was interpolated into JavaScript source with
``repr()``. Where Python's literal grammar and JavaScript's disagree the
evaluate raises, and the old handler assigned the *unresolved* relative href.
``enqueue`` then rejected that for having no origin, so the link disappeared
with nothing written to the log — a crawl that quietly missed pages.
"""

from __future__ import annotations

from urllib.parse import urljoin

import pytest

from web_discovery.config import DiscoveryConfig
from web_discovery.crawler.engine import CrawlEngine
from web_discovery.crawler.session import CrawlSession
from web_discovery.parser.models import ElementSpec, PageSpec

ROOT = "http://example.com"


class StubPage:
    """Resolves URLs the way the browser would, and records the calls."""

    def __init__(self, current: str = f"{ROOT}/", raises: bool = False) -> None:
        self.url = current
        self.raises = raises
        self.evaluated: list[tuple[str, str]] = []

    def evaluate(self, script: str, arg=None):
        self.evaluated.append((script, arg))
        if self.raises:
            raise RuntimeError("SyntaxError: Unexpected token")
        return urljoin(self.url, arg)


def _engine(**overrides) -> CrawlEngine:
    config = DiscoveryConfig(target_url=ROOT, max_depth=3, max_pages=50, **overrides)
    return CrawlEngine(config)


def _spec(*links: tuple[str, str]) -> PageSpec:
    spec = PageSpec(url=f"{ROOT}/", title="t")
    spec.links = [
        ElementSpec(tag="a", element_type="link", href=href, text_content=text)
        for href, text in links
    ]
    return spec


def _session() -> CrawlSession:
    session = CrawlSession(root_url=ROOT, max_depth=3, max_pages=50)
    session.pop()  # the engine has popped the root and is parsing it
    return session


def _queued(session: CrawlSession) -> list[str]:
    return [entry.url for entry in session._queue]


class TestHrefIsPassedAsAnArgument:
    def test_the_href_is_not_interpolated_into_the_script(self):
        """
        The fix itself. The script must be a constant, with the href arriving
        as a separate argument — that is what makes the value's contents
        incapable of changing the code being run.
        """
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("/a", "A")), 0, session)

        script, arg = page.evaluated[0]
        assert arg == "/a"
        assert "/a" not in script
        assert script == "h => new URL(h, document.baseURI).href"

    @pytest.mark.parametrize(
        "href",
        [
            "/plain",
            "/it's",
            '/say"hi"',
            "/both'and\"",
            "/back\\slash",
            "/new\nline",
            "/unicode- separator",
            "/emoji-🎉",
        ],
    )
    def test_awkward_hrefs_are_still_resolved_and_queued(self, href):
        """
        Each of these is a value where a Python literal and a JavaScript literal
        may differ. Under the old code any divergence lost the link silently.
        """
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec((href, "link")), 0, session)

        assert len(session._queue) == 1, f"{href!r} was dropped"

    def test_relative_hrefs_are_made_absolute_before_enqueueing(self):
        page, session = StubPage(current=f"{ROOT}/section/page"), _session()
        _engine()._enqueue_links(page, _spec(("../other", "Other")), 0, session)

        assert _queued(session) == [f"{ROOT}/other"]


class TestResolutionFailure:
    def test_an_unresolvable_href_is_skipped_rather_than_enqueued_relative(self):
        """
        The old fallback assigned the raw relative href, which ``enqueue``
        rejected for having no origin — the same outcome, reached silently and
        by accident. Now it is a deliberate skip.
        """
        page, session = StubPage(raises=True), _session()
        _engine()._enqueue_links(page, _spec(("/a", "A")), 0, session)

        assert list(session._queue) == []

    def test_the_skip_is_logged(self, caplog):
        import logging

        page, session = StubPage(raises=True), _session()
        with caplog.at_level(logging.DEBUG):
            _engine()._enqueue_links(page, _spec(("/a", "A")), 0, session)

        assert any("could not resolve" in r.message for r in caplog.records)

    def test_one_bad_href_does_not_stop_the_rest(self):
        class FlakyPage(StubPage):
            def evaluate(self, script, arg=None):
                if arg == "/bad":
                    raise RuntimeError("nope")
                return urljoin(self.url, arg)

        page, session = FlakyPage(), _session()
        _engine()._enqueue_links(page, _spec(("/bad", "B"), ("/good", "G")), 0, session)

        assert _queued(session) == [f"{ROOT}/good"]


class TestFiltering:
    @pytest.mark.parametrize(
        "text", ["Logout", "Delete account", "Sign out", "Remove item", "Deactivate"]
    )
    def test_destructive_links_are_not_queued(self, text):
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("/danger", text)), 0, session)
        assert list(session._queue) == []

    def test_a_harmless_link_is_queued(self):
        """Negative control for the filter above."""
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("/about", "About us")), 0, session)
        assert _queued(session) == [f"{ROOT}/about"]

    def test_empty_hrefs_are_skipped_without_calling_the_browser(self):
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("", "Empty")), 0, session)

        assert page.evaluated == []
        assert list(session._queue) == []

    def test_cross_origin_links_are_not_queued(self):
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("https://elsewhere.test/x", "Away")), 0, session)
        assert list(session._queue) == []

    def test_a_link_back_to_the_root_is_not_queued(self):
        """Together with the session fix, this is what stops the double crawl."""
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("/", "Home")), 0, session)
        assert list(session._queue) == []

    def test_links_are_enqueued_one_level_deeper(self):
        page, session = StubPage(), _session()
        _engine()._enqueue_links(page, _spec(("/a", "A")), 2, session)
        assert session._queue[0].depth == 3
