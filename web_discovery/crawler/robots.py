"""
robots.txt checker — one decision per origin, cached.

The previous implementation delegated to ``RobotFileParser.read()`` and caught
any exception with the comment "allowing all". It did not allow all. A parser
whose ``read()`` raised is left with ``last_checked == 0``, and CPython's
``can_fetch`` returns **False** in that state on purpose ("until the robots.txt
file has been read ... we must assume that no url is allowable"). So a DNS
failure, a refused connection or a timeout silently disallowed the entire
crawl, and the two log lines contradicted each other in the same output:

    [robots] could not read http://host/robots.txt — allowing all
    [robots] blocked: http://host/page

``crawler/engine.py`` skips every URL ``is_allowed`` rejects, so the visible
symptom was a crawl that found nothing, with per-URL INFO lines that read as
though the site had refused rather than as though robots.txt was unreadable.
Nothing caught it because this module was at 0% test coverage.

Fetching is now done here rather than inside the stdlib parser, for two
reasons: ``RobotFileParser.read()`` takes no timeout, so an unresponsive
robots.txt could stall a crawl indefinitely; and doing it here makes every
outcome below reachable in a test without a network.

Outcomes follow RFC 9309 §2.3.1 where it has an opinion:

===========================  ==========  ================================
robots.txt fetch             verdict     why
===========================  ==========  ================================
200                          parse it    normal operation
401 / 403                    disallow    §2.3.1.3, access is restricted
other 4xx                    allow all   §2.3.1.2, treated as absent
5xx                          disallow    §2.3.1.4, unreachable
network error / timeout      allow       undefined by the RFC; see below
===========================  ==========  ================================

The last row is a judgement, not a standard. This tool is pointed at an
application you own, usually one that serves no robots.txt at all. Refusing to
crawl because the check itself failed turns a transient network blip into a
silent empty run, which is the failure this module just had. It allows, and it
says so at WARNING so the decision is visible rather than inferred from an
empty result.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

# RFC 9309 §2.2.1: the product token a robots.txt writes carries no version, and
# RobotFileParser splits our agent on "/" before matching. A site wanting to name
# this crawler writes PRODUCT_TOKEN; USER_AGENT is what goes on the wire.
PRODUCT_TOKEN = "wiki-discovery-bot"
USER_AGENT = f"{PRODUCT_TOKEN}/1.0"
DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RobotsFetch:
    """The result of trying to read one origin's robots.txt."""

    status: int | None  # None when the request never produced an HTTP status
    body: str = ""
    error: str = ""


def fetch_robots(url: str, timeout: float, user_agent: str = USER_AGENT) -> RobotsFetch:
    """Retrieve a robots.txt. Never raises; the outcome is in the return value."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
            return RobotsFetch(status=response.status, body=raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return RobotsFetch(status=exc.code)
    except Exception as exc:  # noqa: BLE001 — URLError, timeout, bad scheme, anything
        return RobotsFetch(status=None, error=f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class _Decision:
    """What one origin's robots.txt permits."""

    parser: RobotFileParser | None  # None when the verdict is unconditional
    allow_everything: bool = False
    deny_everything: bool = False

    def permits(self, url: str, user_agent: str) -> bool:
        if self.deny_everything:
            return False
        if self.allow_everything or self.parser is None:
            return True
        return self.parser.can_fetch(user_agent, url)


class RobotsChecker:
    """Cache-backed robots.txt checker. One decision per origin."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_S,
        user_agent: str = USER_AGENT,
        fetch: Callable[[str, float, str], RobotsFetch] | None = None,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._fetch = fetch or fetch_robots
        self._cache: dict[str, _Decision] = {}

    def is_allowed(self, url: str) -> bool:
        """Whether the URL may be crawled according to its origin's robots.txt."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.warning("[robots] not an absolute URL, refusing to crawl: %s", url)
            return False

        origin = f"{parsed.scheme}://{parsed.netloc}"
        decision = self._decide(origin)
        allowed = decision.permits(url, self._user_agent)
        if not allowed:
            logger.debug("[robots] disallowed by %s/robots.txt: %s", origin, url)
        return allowed

    def _decide(self, origin: str) -> _Decision:
        cached = self._cache.get(origin)
        if cached is not None:
            return cached

        robots_url = f"{origin}/robots.txt"
        result = self._fetch(robots_url, self._timeout, self._user_agent)
        decision = self._interpret(robots_url, result)
        self._cache[origin] = decision
        return decision

    def _interpret(self, robots_url: str, result: RobotsFetch) -> _Decision:
        if result.status is None:
            # The check itself failed. Allowing is a deliberate choice — see the
            # module docstring — and it is logged at WARNING because an empty
            # crawl caused by a silent disallow is exactly what went unnoticed.
            logger.warning(
                "[robots] could not read %s (%s) — allowing this origin. "
                "Set WD_RESPECT_ROBOTS=false to skip the check entirely.",
                robots_url,
                result.error,
            )
            return _Decision(parser=None, allow_everything=True)

        if result.status in (401, 403):
            logger.warning(
                "[robots] %s returned %d — treating the whole origin as disallowed "
                "(RFC 9309 §2.3.1.3)",
                robots_url,
                result.status,
            )
            return _Decision(parser=None, deny_everything=True)

        if 500 <= result.status < 600:
            logger.warning(
                "[robots] %s returned %d — treating the whole origin as disallowed "
                "(RFC 9309 §2.3.1.4)",
                robots_url,
                result.status,
            )
            return _Decision(parser=None, deny_everything=True)

        if 400 <= result.status < 500:
            logger.debug(
                "[robots] %s returned %d — no robots.txt, allowing", robots_url, result.status
            )
            return _Decision(parser=None, allow_everything=True)

        parser = RobotFileParser()
        parser.parse(result.body.splitlines())
        logger.debug("[robots] loaded %s", robots_url)
        return _Decision(parser=parser)
