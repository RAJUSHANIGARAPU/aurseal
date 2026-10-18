"""The site's primary navigation landmark."""

from __future__ import annotations

import re

from playwright.sync_api import Locator, Page


class Navigation:
    def __init__(self, page: Page) -> None:
        self.root = page.get_by_role("navigation").first

    @property
    def links(self) -> Locator:
        return self.root.get_by_role("link")

    @property
    def named_links(self) -> Locator:
        """Links whose accessible name (text, aria-label, image alt) is not blank."""
        return self.root.get_by_role("link", name=re.compile(r"\S"))
