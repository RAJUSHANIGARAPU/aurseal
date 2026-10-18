"""The pages and page states the suite scans.

A scan sees only what the page shows at that moment, so a menu, a modal or a form's error
state is a state of its own: add a row, and a ``prepare`` step that brings the page there.
Paths resolve against the target URL; the first row also hosts the live positive control.
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


PAGES: tuple[PageState, ...] = (PageState("home", "/"),)
