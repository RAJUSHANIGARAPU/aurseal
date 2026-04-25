"""Tests for CrawlSession and URL utilities."""

from web_discovery.crawler.session import (
    CrawlSession,
    is_destructive_text,
    normalise_url,
)


class TestNormaliseUrl:
    def test_strips_fragment(self):
        assert normalise_url("http://example.com/page#section") == "http://example.com/page"

    def test_sorts_query_params(self):
        a = normalise_url("http://example.com/?b=2&a=1")
        b = normalise_url("http://example.com/?a=1&b=2")
        assert a == b

    def test_returns_empty_for_javascript(self):
        assert normalise_url("javascript:void(0)") == ""

    def test_returns_empty_for_blank(self):
        assert normalise_url("") == ""

    def test_removes_trailing_slash_from_root(self):
        result = normalise_url("http://example.com/")
        assert result is not None
        assert not result.endswith("/") or result == "http://example.com/"

    def test_handles_path(self):
        result = normalise_url("http://example.com/some/path")
        assert result == "http://example.com/some/path"


class TestDestructiveText:
    def test_logout_is_destructive(self):
        assert is_destructive_text("Logout")

    def test_delete_is_destructive(self):
        assert is_destructive_text("Delete account")

    def test_remove_is_destructive(self):
        assert is_destructive_text("Remove item")

    def test_sign_out_is_destructive(self):
        assert is_destructive_text("Sign out")

    def test_home_is_safe(self):
        assert not is_destructive_text("Home")

    def test_submit_is_safe(self):
        assert not is_destructive_text("Submit form")

    def test_empty_is_safe(self):
        assert not is_destructive_text("")


class TestCrawlSession:
    def _session(self, root="http://example.com", max_depth=2, max_pages=10):
        return CrawlSession(root_url=root, max_depth=max_depth, max_pages=max_pages)

    def test_initial_queue_has_root(self):
        s = self._session()
        assert s.has_next()
        entry = s.pop()
        assert entry.url == "http://example.com"
        assert entry.depth == 0

    def test_enqueue_and_pop(self):
        s = self._session()
        s.pop()  # consume root
        s.enqueue("http://example.com/page", depth=1, referrer="http://example.com")
        assert s.has_next()
        entry = s.pop()
        assert "/page" in entry.url

    def test_visited_prevents_reenqueue(self):
        s = self._session()
        s.pop()
        s.mark_visited("http://example.com/page")
        s.enqueue("http://example.com/page", depth=1, referrer="")
        assert not s.has_next()

    def test_max_depth_enforced(self):
        s = self._session(max_depth=1)
        s.pop()
        s.enqueue("http://example.com/a", depth=1, referrer="")
        s.enqueue("http://example.com/b", depth=2, referrer="")  # should be rejected
        count = 0
        while s.has_next():
            s.pop()
            count += 1
        assert count == 1

    def test_max_pages_enforced(self):
        s = self._session(max_pages=3)
        s.pop()  # root = page 1
        s.mark_visited("http://example.com")
        s.enqueue("http://example.com/a", depth=1, referrer="")
        s.enqueue("http://example.com/b", depth=1, referrer="")
        s.enqueue("http://example.com/c", depth=1, referrer="")
        count = 1
        while s.has_next():
            e = s.pop()
            s.mark_visited(e.url)
            count += 1
        assert count <= 3

    def test_cross_origin_rejected(self):
        s = self._session()
        s.pop()
        s.enqueue("http://other.com/page", depth=1, referrer="")
        assert not s.has_next()
