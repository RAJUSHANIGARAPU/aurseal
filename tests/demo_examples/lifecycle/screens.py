"""Read a rendered screen without a browser: the text of an element found by ``data-testid``.

Used where an example only needs to know what a screen offers (which label a button
carries), not to click through it; the clicking itself is driven over the API.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


class _TextOf(HTMLParser):
    def __init__(self, testid: str) -> None:
        super().__init__()
        self.testid = testid
        self.depth = 0
        self.found: list[str] = []
        self.buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        if self.depth:
            self.depth += 1
        elif dict(attrs).get("data-testid") == self.testid:
            self.depth = 1
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or not self.depth:
            return
        self.depth -= 1
        if not self.depth:
            self.found.append(" ".join("".join(self.buffer).split()))

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.buffer.append(data)


def element_texts(html: str, testid: str) -> list[str]:
    """Whitespace-normalised text of every element carrying ``data-testid=testid``."""
    parser = _TextOf(testid)
    parser.feed(html)
    parser.close()
    return parser.found


def match_label_variant(text: str, variants: Sequence[str]) -> str:
    """The accepted variant ``text`` is, logged; ``LookupError`` naming all variants otherwise.

    A renamed control keeps working as long as its new label is one of the known variants;
    an unknown label fails loudly instead of the flow silently clicking nothing.
    """
    for variant in variants:
        if text.strip().casefold() == variant.casefold():
            logger.info("label variant matched: %r", variant)
            return variant
    raise LookupError(f"label {text!r} is none of the accepted variants {list(variants)}")
