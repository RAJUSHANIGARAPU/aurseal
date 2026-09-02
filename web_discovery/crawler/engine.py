"""Playwright-based crawl engine.

Navigates the target app like a real user:
- BFS traversal up to max_depth
- Waits for networkidle after each navigation (SPA-safe)
- Detects and skips destructive actions (logout, delete, etc.)
- Respects robots.txt when RESPECT_ROBOTS is True
- Never follows external links (same-origin only)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from web_discovery.crawler.robots import RobotsChecker
from web_discovery.crawler.session import CrawlSession, is_destructive_text, normalise_url
from web_discovery.parser.dom_parser import DomParser
from web_discovery.parser.models import PageSpec

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

    from web_discovery.config import DiscoveryConfig

logger = logging.getLogger(__name__)


class CrawlEngine:
    """Drives a full BFS crawl of the target application."""

    def __init__(self, config: DiscoveryConfig) -> None:
        self._config = config
        self._robots = RobotsChecker() if config.respect_robots else _AlwaysAllow()
        self._parser = DomParser()

    def crawl(self, browser: Browser) -> list[PageSpec]:
        """Run the full crawl and return all discovered page specs."""
        target = self._config.target_url
        if not target:
            logger.error("[crawl] no target URL configured")
            return []

        session = CrawlSession(
            root_url=target,
            max_depth=self._config.max_depth,
            max_pages=self._config.max_pages,
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        context.set_default_timeout(self._config.page_timeout_ms)

        results: list[PageSpec] = []

        try:
            page = context.new_page()
            self._setup_listeners(page)

            if self._config.auth_support:
                self._authenticate(page, session)

            while session.has_next():
                entry = session.pop()
                url = entry.url

                if self._config.respect_robots and not self._robots.is_allowed(url):
                    logger.info("[crawl] robots.txt blocked: %s", url)
                    continue

                logger.info(
                    "[crawl] depth=%d [%d/%d] %s",
                    entry.depth,
                    session.pages_crawled + 1,
                    self._config.max_pages,
                    url,
                )

                spec = self._crawl_page(page, url, entry.depth, session)
                if spec:
                    results.append(spec)
                    session.mark_visited(url)

        except Exception as exc:  # noqa: BLE001
            logger.error("[crawl] fatal error: %s", exc)
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass

        logger.info(
            "[crawl] complete — %d pages crawled, %d specs returned",
            session.pages_crawled,
            len(results),
        )
        return results

    # ------------------------------------------------------------------

    def _crawl_page(
        self,
        page: Page,
        url: str,
        depth: int,
        session: CrawlSession,
    ) -> PageSpec | None:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self._config.page_timeout_ms)
            # Extra wait for SPA rendering (React/Angular hydration)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self._config.wait_after_nav_ms / 1000)

            spec = self._parser.parse(page)
            spec.depth = depth

            # Enqueue all same-origin links found on this page
            if depth < self._config.max_depth:
                self._enqueue_links(page, spec, depth, session)

            return spec

        except Exception as exc:  # noqa: BLE001
            logger.warning("[crawl] failed to crawl %s: %s", url, exc)
            return None

    def _enqueue_links(
        self,
        page: Page,
        spec: PageSpec,
        current_depth: int,
        session: CrawlSession,
    ) -> None:
        for link in spec.links:
            href = link.href or ""
            if not href:
                continue

            # The href is passed as an argument rather than interpolated into
            # the script source. `repr()` produces a Python literal, not a
            # JavaScript one, and where the two disagree the evaluate raises,
            # the old fallback assigned the still-relative href, and `enqueue`
            # then rejected it for having no origin — so the link vanished with
            # nothing logged.
            try:
                abs_url = page.evaluate("h => new URL(h, document.baseURI).href", href)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[crawl] could not resolve %r against %s: %s", href, page.url, exc)
                continue

            if is_destructive_text(link.text_content):
                logger.debug("[crawl] skipping destructive link: %s", link.text_content)
                continue

            norm = normalise_url(abs_url)
            if norm and not session.is_visited(norm):
                session.enqueue(norm, depth=current_depth + 1, referrer=page.url)

    def _authenticate(self, page: Page, session: CrawlSession) -> None:
        """Perform form-based authentication if configured."""
        auth_url = self._config.auth_url or self._config.target_url
        try:
            logger.info("[crawl] authenticating at %s", auth_url)
            page.goto(auth_url, wait_until="domcontentloaded")
            time.sleep(1)

            # Try common username/password field patterns
            for sel in (
                "input[type=email]",
                "input[name=email]",
                "input[name=username]",
                "input[name=login]",
                "#email",
                "#username",
            ):
                if page.locator(sel).count():
                    page.locator(sel).fill(self._config.auth_username)
                    break

            for sel in (
                "input[type=password]",
                "input[name=password]",
                "#password",
            ):
                if page.locator(sel).count():
                    page.locator(sel).fill(self._config.auth_password)
                    break

            for sel in (
                "button[type=submit]",
                "input[type=submit]",
                "button:has-text('Login')",
                "button:has-text('Sign in')",
            ):
                if page.locator(sel).count():
                    page.locator(sel).click()
                    break

            page.wait_for_load_state("networkidle", timeout=10_000)
            time.sleep(1)
            session.mark_visited(auth_url)
            logger.info("[crawl] authentication complete — at %s", page.url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[crawl] authentication failed: %s — continuing unauthenticated", exc)

    @staticmethod
    def _setup_listeners(page: Page) -> None:
        page.on(
            "console",
            lambda msg: (
                logger.debug("[browser-console] %s: %s", msg.type, msg.text)
                if msg.type == "error"
                else None
            ),
        )
        page.on("pageerror", lambda err: logger.debug("[browser-error] %s", err))


class _AlwaysAllow:
    def is_allowed(self, url: str) -> bool:  # noqa: ARG002
        return True
