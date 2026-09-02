"""
Budget and dedup behaviour of CrawlSession, driven the way CrawlEngine drives it.

``test_session.py`` already covers the queue, and its ``test_max_pages_enforced``
passed throughout — because it calls ``mark_visited`` after every ``pop``.
``CrawlEngine`` does not: it marks a page visited only when the page parsed, so
on any site where pages time out the budget was never applied. With
``max_pages=5`` and 51 queued URLs that all fail, the old session popped all 51.

The lesson is the same one the OpenAI stub taught in agent-lens: a test shaped
around the happy path of the implementation proves nothing about the caller. So
these tests reproduce the caller's real patterns — including the failing one.
"""

from __future__ import annotations

import pytest

from web_discovery.crawler.session import CrawlSession

ROOT = "http://example.com"


def _session(root: str = ROOT, max_depth: int = 3, max_pages: int = 10) -> CrawlSession:
    return CrawlSession(root_url=root, max_depth=max_depth, max_pages=max_pages)


class TestBudgetHoldsWhateverTheCallerDoes:
    def test_budget_holds_when_every_page_fails(self):
        """
        The engine's failure path: pop, try to crawl, get nothing, never mark.

        max_pages is a limit on requests sent to someone else's server. A page
        that timed out still cost them a request, so it has to count.
        """
        session = _session(max_pages=5)
        for i in range(50):
            session.enqueue(f"{ROOT}/p{i}", depth=1)

        popped = 0
        while session.has_next():
            session.pop()  # no mark_visited — the page did not parse
            popped += 1
            assert popped < 200, "the budget is not bounding the loop at all"

        assert popped == 5
        assert session.pages_crawled == 0

    def test_budget_holds_when_every_page_succeeds(self):
        session = _session(max_pages=5)
        for i in range(50):
            session.enqueue(f"{ROOT}/p{i}", depth=1)

        popped = 0
        while session.has_next():
            entry = session.pop()
            session.mark_visited(entry.url)
            popped += 1

        assert popped == 5
        assert session.pages_crawled == 5

    def test_budget_holds_when_some_pages_fail(self):
        session = _session(max_pages=6)
        for i in range(50):
            session.enqueue(f"{ROOT}/p{i}", depth=1)

        popped = 0
        while session.has_next():
            entry = session.pop()
            if popped % 2 == 0:
                session.mark_visited(entry.url)
            popped += 1

        assert popped == 6
        assert session.pages_crawled == 3

    @pytest.mark.parametrize("max_pages", [1, 2, 7, 25])
    def test_the_budget_is_exactly_max_pages(self, max_pages):
        session = _session(max_pages=max_pages)
        for i in range(100):
            session.enqueue(f"{ROOT}/p{i}", depth=1)

        popped = 0
        while session.has_next():
            session.pop()
            popped += 1
        assert popped == max_pages

    def test_a_short_queue_still_ends(self):
        """The budget is a ceiling, not a target — don't spin on an empty queue."""
        session = _session(max_pages=100)
        session.enqueue(f"{ROOT}/only", depth=1)

        popped = 0
        while session.has_next():
            session.pop()
            popped += 1
        assert popped == 2  # root + the one link

    def test_pages_attempted_and_pages_crawled_are_different_numbers(self):
        session = _session(max_pages=10)
        session.enqueue(f"{ROOT}/a", depth=1)
        session.enqueue(f"{ROOT}/b", depth=1)

        session.pop()
        entry = session.pop()
        session.mark_visited(entry.url)
        session.pop()

        assert session.pages_attempted == 3
        assert session.pages_crawled == 1


class TestTheRootIsNotCrawledTwice:
    def test_a_link_back_to_the_root_is_not_enqueued(self):
        """
        A homepage logo pointing at "/" used to re-queue the root.

        Links are enqueued while the page is still being parsed, which happens
        before the engine marks that page visited — so the root, seeded straight
        into the queue by __init__, was not yet in the visited set.
        """
        session = _session()
        session.pop()  # engine pops the root and starts parsing it

        assert session.enqueue(f"{ROOT}/", depth=1) is False
        assert session.has_next() is False

    @pytest.mark.parametrize(
        "root,link",
        [
            ("http://example.com", "http://example.com/"),
            ("http://example.com/", "http://example.com"),
            ("http://example.com/", "http://example.com/"),
            ("http://example.com", "http://example.com"),
            ("http://example.com/app/", "http://example.com/app"),
        ],
    )
    def test_root_and_link_forms_are_treated_as_the_same_page(self, root, link):
        """Trailing slashes must not smuggle the root back into the queue."""
        session = _session(root=root)
        session.pop()
        assert session.enqueue(link, depth=1) is False

    def test_the_root_is_still_the_first_thing_popped(self):
        """Marking the root as seen must not remove it from the queue."""
        session = _session()
        assert session.has_next()
        assert session.pop().url == ROOT

    def test_a_genuinely_different_page_is_still_enqueued(self):
        """
        Negative control.

        If pre-marking the root started rejecting everything, every test above
        would still pass — they all assert that something is refused.
        """
        session = _session()
        session.pop()
        assert session.enqueue(f"{ROOT}/somewhere-else", depth=1) is True
        assert session.has_next() is True
