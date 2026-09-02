"""CrawlSession — tracks state across the crawl to prevent loops and respect limits."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_DESTRUCTIVE_PATTERNS = re.compile(
    r"\b(delete|remove|destroy|logout|log.?out|sign.?out|deactivate|unsubscribe"
    r"|cancel|purge|wipe|reset|clear.all)\b",
    re.IGNORECASE,
)

_SKIP_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".zip",
    ".tar",
    ".gz",
    ".mp4",
    ".webm",
}


@dataclass
class QueueEntry:
    url: str
    depth: int
    referrer: str = ""


class CrawlSession:
    """Immutable-ish state for one crawl run.

    Tracks:
    - visited URLs (normalised, deduped)
    - crawl queue (BFS order)
    - discovered page count
    """

    def __init__(self, root_url: str, max_depth: int, max_pages: int) -> None:
        self.root_url = root_url
        self.origin = _origin(root_url)
        self.max_depth = max_depth
        self.max_pages = max_pages
        self._visited: set[str] = set()
        self._queue: deque[QueueEntry] = deque()
        self._queue.append(QueueEntry(url=root_url, depth=0))
        # The root is seeded straight into the queue, so nothing else records
        # that it has been seen. Without this, a homepage carrying a logo link
        # to "/" re-enqueues the root and it gets crawled twice — links are
        # enqueued while the page is being parsed, which is before the caller
        # gets a chance to mark it visited.
        self._visited.add(normalise_url(root_url) or root_url)
        self.pages_attempted = 0
        self.pages_crawled = 0

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def has_next(self) -> bool:
        return bool(self._queue) and self.pages_attempted < self.max_pages

    def pop(self) -> QueueEntry:
        """
        Take the next URL to crawl, counting it against the page budget.

        The budget is spent here rather than on success, because it is a limit
        on how much traffic this crawler sends to a target — a page that timed
        out still cost the target a request. Counting it in ``mark_visited``
        made the limit depend on the caller remembering to call that on every
        path: ``CrawlEngine`` only calls it when a page parses, so on a site
        where pages fail the budget was not applied at all. Measured before the
        change, with ``max_pages=5`` and 51 queued URLs that all fail: 51 popped.
        """
        self.pages_attempted += 1
        return self._queue.popleft()

    def enqueue(self, url: str, depth: int, referrer: str = "") -> bool:
        """Try to enqueue a URL. Returns True if it was added, False if skipped."""
        norm = normalise_url(url)
        if not norm:
            return False
        if _origin(norm) != self.origin:
            return False
        if norm in self._visited:
            return False
        if depth > self.max_depth:
            return False
        if _has_skip_extension(norm):
            return False
        self._visited.add(norm)
        self._queue.append(QueueEntry(url=norm, depth=depth, referrer=referrer))
        return True

    def mark_visited(self, url: str) -> None:
        """Record a page as successfully crawled. Does not affect the budget."""
        self._visited.add(normalise_url(url) or url)
        self.pages_crawled += 1

    def is_visited(self, url: str) -> bool:
        return (normalise_url(url) or url) in self._visited

    @property
    def visited_count(self) -> int:
        return len(self._visited)


# ------------------------------------------------------------------
# URL utilities
# ------------------------------------------------------------------


def normalise_url(url: str) -> str:
    """Strip fragment, sort query params, remove trailing slash for non-root paths."""
    if not url or url.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    try:
        parsed = urlparse(url)
        # Sort query params for canonical form
        sorted_query = urlencode(sorted(parse_qsl(parsed.query)))
        path = parsed.path.rstrip("/") or "/"
        clean = urlunparse((parsed.scheme, parsed.netloc, path, "", sorted_query, ""))
        return clean
    except Exception:  # noqa: BLE001
        return ""


def _origin(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:  # noqa: BLE001
        return ""


def _has_skip_extension(url: str) -> bool:
    try:
        path = urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)
    except Exception:  # noqa: BLE001
        return False


def is_destructive_text(text: str) -> bool:
    """True if element text suggests a destructive action that should not be clicked."""
    return bool(_DESTRUCTIVE_PATTERNS.search(text or ""))
