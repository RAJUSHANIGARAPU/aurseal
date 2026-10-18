"""The pages the suite scans: the demo's four pages, in their fixed and broken versions.

``PAGES`` (the accessible "after" site) is what the gate judges. ``BEFORE`` (the same four
pages, inaccessible on purpose) is the positive control: a scanner that finds nothing
there is broken, not passing. Paths resolve against the target URL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Page


def _loaded(page: Page) -> None:
    """Nothing to do: scan the page as it loads."""


@dataclass(frozen=True)
class PageState:
    id: str
    path: str
    prepare: Callable[[Page], None] = _loaded


NAMES = ("home", "news", "tickets", "survey")

PAGES: tuple[PageState, ...] = tuple(PageState(name, f"after/{name}.html") for name in NAMES)
BEFORE: tuple[PageState, ...] = tuple(PageState(name, f"before/{name}.html") for name in NAMES)

#: Rules each "before" page breaks on purpose (observed 2026-10-18 with axe 4.12). The
#: control asserts these are found; more is fine, fewer means the scanner lost its sight.
PLANTED: dict[str, frozenset[str]] = {
    "home": frozenset({"image-alt", "html-has-lang", "link-name", "select-name", "color-contrast"}),
    "news": frozenset({"image-alt", "html-has-lang", "link-name", "select-name"}),
    "tickets": frozenset({"image-alt", "html-has-lang", "link-name", "select-name"}),
    "survey": frozenset({"image-alt", "html-has-lang", "link-name", "select-name", "label"}),
}
