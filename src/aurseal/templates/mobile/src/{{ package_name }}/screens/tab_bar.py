"""The bottom tab bar: each tab's accessibility id and the screen it opens."""

from __future__ import annotations

from .base_screen import BaseScreen, Locator, accessibility_id

#: Tab accessibility id -> accessibility id of the screen it opens (the demo app's).
TABS: dict[str, str] = {
    "Home": "Home-screen",
    "Login": "Login-screen",
    "Forms": "Forms-screen",
    "Swipe": "Swipe-screen",
    "Drag": "Drag-drop-screen",
}


class TabBar(BaseScreen):
    marker = accessibility_id("Home")

    @staticmethod
    def tab(name: str) -> Locator:
        return accessibility_id(name)

    @staticmethod
    def screen_for(name: str) -> Locator:
        return accessibility_id(TABS[name])

    def open(self, name: str) -> None:
        self.tap(self.tab(name))
