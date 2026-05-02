"""robots.txt checker — stdlib urllib.robotparser, cached per origin."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

_USER_AGENT = "wiki-discovery-bot/1.0"


class RobotsChecker:
    """Cache-backed robots.txt checker. One parser per origin."""

    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL is allowed for crawling per robots.txt."""
        try:
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            parser = self._get_parser(origin)
            allowed = parser.can_fetch(_USER_AGENT, url)
            if not allowed:
                logger.debug("[robots] blocked: %s", url)
            return allowed
        except Exception as exc:  # noqa: BLE001
            logger.debug("[robots] error checking %s: %s — allowing", url, exc)
            return True

    def _get_parser(self, origin: str) -> RobotFileParser:
        if origin not in self._cache:
            parser = RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            try:
                parser.set_url(robots_url)
                parser.read()
                logger.debug("[robots] loaded: %s", robots_url)
            except Exception:  # noqa: BLE001
                logger.debug("[robots] could not read %s — allowing all", robots_url)
            self._cache[origin] = parser
        return self._cache[origin]
