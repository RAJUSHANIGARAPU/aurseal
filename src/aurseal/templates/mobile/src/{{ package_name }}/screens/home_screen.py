"""The screen the app opens on."""

from __future__ import annotations

from .base_screen import BaseScreen, accessibility_id


class HomeScreen(BaseScreen):
    marker = accessibility_id("Home-screen")
