"""The landing page of the application under test."""

from __future__ import annotations

from playwright.sync_api import Locator

from ..components.navigation import Navigation
from .base_page import BasePage


class HomePage(BasePage):
    path = "/"

    @property
    def navigation(self) -> Navigation:
        return Navigation(self.page)

    @property
    def main_content(self) -> Locator:
        return self.page.get_by_role("main")

    def fits_viewport_width(self) -> bool:
        """True when nothing forces a horizontal scroll bar at the current viewport."""
        return bool(
            self.page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
        )
