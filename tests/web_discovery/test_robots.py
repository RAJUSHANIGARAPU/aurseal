"""
Tests for the robots.txt checker.

This module was at 0% coverage while ``tests/web_discovery/`` already held
around ninety tests for the models, config and generators around it — the
components that decide nothing. The defect that hid there: on any network
failure the checker disallowed the entire crawl while logging "allowing all",
so a crawl found nothing and the log said the site had blocked it.

Every case below is reachable offline because fetching is injected.
"""

from __future__ import annotations

import logging

import pytest

from web_discovery.crawler.robots import (
    PRODUCT_TOKEN,
    USER_AGENT,
    RobotsChecker,
    RobotsFetch,
    fetch_robots,
)

ORIGIN = "https://example.com"
PAGE = f"{ORIGIN}/some/page"


def _checker(result: RobotsFetch, **kwargs) -> RobotsChecker:
    calls: list[tuple[str, float, str]] = []

    def fake_fetch(url: str, timeout: float, user_agent: str) -> RobotsFetch:
        calls.append((url, timeout, user_agent))
        return result

    checker = RobotsChecker(fetch=fake_fetch, **kwargs)
    checker.calls = calls  # type: ignore[attr-defined]
    return checker


class TestTheRegressionThatWasShipped:
    def test_network_failure_allows_instead_of_silently_blocking_everything(self):
        """
        The whole reason this file exists.

        A DNS failure, refused connection or timeout used to leave an unread
        RobotFileParser in the cache, and ``can_fetch`` on an unread parser
        returns False — so the crawler skipped every URL and reported the site
        as having blocked it.
        """
        checker = _checker(
            RobotsFetch(status=None, error="URLError: [Errno 8] nodename nor servname")
        )
        assert checker.is_allowed(PAGE) is True

    def test_the_failure_is_logged_loudly_not_at_debug(self, caplog):
        """
        A silent empty crawl is the symptom that went unnoticed for as long as
        this bug existed. If the check cannot run, that has to be visible at
        default log level, not buried at DEBUG.
        """
        checker = _checker(RobotsFetch(status=None, error="timed out"))
        with caplog.at_level(logging.WARNING):
            checker.is_allowed(PAGE)

        assert any("could not read" in r.message for r in caplog.records)
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_the_log_no_longer_contradicts_the_verdict(self, caplog):
        """
        The old output said "allowing all" and then "blocked" for the same URL.
        Whatever the verdict is, the log has to agree with it.
        """
        checker = _checker(RobotsFetch(status=None, error="boom"))
        with caplog.at_level(logging.DEBUG):
            allowed = checker.is_allowed(PAGE)

        text = " ".join(r.message for r in caplog.records)
        assert allowed is True
        assert "disallowed" not in text


class TestHttpStatusHandling:
    def test_200_applies_the_rules_in_the_file(self):
        body = "User-agent: *\nDisallow: /private\n"
        checker = _checker(RobotsFetch(status=200, body=body))

        assert checker.is_allowed(f"{ORIGIN}/public") is True
        assert checker.is_allowed(f"{ORIGIN}/private/thing") is False

    def test_200_with_a_rule_naming_our_agent(self):
        body = f"User-agent: {PRODUCT_TOKEN}\nDisallow: /nope\n\nUser-agent: *\nDisallow:\n"
        checker = _checker(RobotsFetch(status=200, body=body))

        assert checker.is_allowed(f"{ORIGIN}/nope") is False
        assert checker.is_allowed(f"{ORIGIN}/yes") is True

    def test_a_rule_written_with_our_version_suffix_does_not_match(self):
        """
        Worth pinning rather than discovering later.

        ``RobotFileParser`` splits our agent on "/" before matching, so it
        compares the robots.txt token against "wiki-discovery-bot" — a file
        that writes "wiki-discovery-bot/1.0" never matches. That is the correct
        reading (RFC 9309 §2.2.1: a product token carries no version), but it
        means our USER_AGENT string and the token a site must write are not the
        same text, which is easy to get wrong in either direction.
        """
        assert USER_AGENT == f"{PRODUCT_TOKEN}/1.0"

        body = f"User-agent: {USER_AGENT}\nDisallow: /nope\n"
        assert _checker(RobotsFetch(status=200, body=body)).is_allowed(f"{ORIGIN}/nope") is True

        body = f"User-agent: {PRODUCT_TOKEN}\nDisallow: /nope\n"
        assert _checker(RobotsFetch(status=200, body=body)).is_allowed(f"{ORIGIN}/nope") is False

    def test_200_empty_body_allows(self):
        assert _checker(RobotsFetch(status=200, body="")).is_allowed(PAGE) is True

    @pytest.mark.parametrize("status", [401, 403])
    def test_restricted_robots_disallows_the_origin(self, status):
        """RFC 9309 §2.3.1.3 — if robots.txt itself is protected, stay out."""
        assert _checker(RobotsFetch(status=status)).is_allowed(PAGE) is False

    @pytest.mark.parametrize("status", [400, 404, 410, 429])
    def test_other_4xx_means_no_robots_file_so_allow(self, status):
        """RFC 9309 §2.3.1.2 — unavailable is treated as absent."""
        assert _checker(RobotsFetch(status=status)).is_allowed(PAGE) is True

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_5xx_disallows(self, status):
        """RFC 9309 §2.3.1.4 — unreachable means assume complete disallow."""
        assert _checker(RobotsFetch(status=status)).is_allowed(PAGE) is False


class TestCaching:
    def test_one_fetch_per_origin(self):
        checker = _checker(RobotsFetch(status=200, body="User-agent: *\nDisallow:\n"))
        for path in ("/a", "/b", "/c"):
            checker.is_allowed(f"{ORIGIN}{path}")
        assert len(checker.calls) == 1  # type: ignore[attr-defined]

    def test_different_origins_are_fetched_separately(self):
        checker = _checker(RobotsFetch(status=200, body=""))
        checker.is_allowed("https://a.example.com/x")
        checker.is_allowed("https://b.example.com/x")
        assert len(checker.calls) == 2  # type: ignore[attr-defined]

    def test_scheme_is_part_of_the_origin(self):
        checker = _checker(RobotsFetch(status=200, body=""))
        checker.is_allowed("http://example.com/x")
        checker.is_allowed("https://example.com/x")
        assert len(checker.calls) == 2  # type: ignore[attr-defined]

    def test_the_timeout_and_agent_are_passed_to_the_fetcher(self):
        checker = _checker(RobotsFetch(status=200, body=""), timeout=1.5, user_agent="ua/9")
        checker.is_allowed(PAGE)
        url, timeout, agent = checker.calls[0]  # type: ignore[attr-defined]
        assert url == f"{ORIGIN}/robots.txt"
        assert timeout == 1.5
        assert agent == "ua/9"


class TestMalformedInput:
    @pytest.mark.parametrize("url", ["", "not-a-url", "/relative/path", "javascript:void(0)"])
    def test_a_url_with_no_origin_is_refused_without_fetching(self, url):
        """There is no robots.txt to consult, so there is nothing to permit."""
        checker = _checker(RobotsFetch(status=200, body=""))
        assert checker.is_allowed(url) is False
        assert checker.calls == []  # type: ignore[attr-defined]


class TestFetchRobots:
    """The real fetcher. Kept offline — only its failure path is exercised."""

    def test_unreachable_host_reports_an_error_rather_than_raising(self):
        result = fetch_robots("http://no-such-host-abcxyz.invalid/robots.txt", timeout=2.0)
        assert result.status is None
        assert result.error

    def test_a_bad_scheme_is_an_error_not_an_exception(self):
        result = fetch_robots("gopher://example.com/robots.txt", timeout=2.0)
        assert result.status is None
        assert result.error
