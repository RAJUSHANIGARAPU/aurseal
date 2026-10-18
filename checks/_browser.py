"""A short-lived browser for engines that run outside a pytest ``page`` fixture (plugin, CLI).

``CHECKS_BROWSER_CHANNEL`` picks an installed browser channel (``chrome``, ``msedge``);
unset uses Playwright's bundled build. A launch failure raises ``BrowserUnavailable`` so
the caller can report ``unknown`` instead of a verdict; errors raised by the caller's own
block pass through unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from core.chrome_launch import with_chrome_args

CHANNEL_ENV = "CHECKS_BROWSER_CHANNEL"


class BrowserUnavailable(RuntimeError):
    """No browser could be started."""


@contextmanager
def browser_context(
    browser_name: str = "chromium",
    *,
    channel: str | None = None,
    viewport: dict[str, int] | None = None,
    device_scale_factor: float = 1,
) -> Iterator[Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - playwright is a base requirement
        raise BrowserUnavailable("playwright is not installed") from exc
    channel = channel if channel is not None else (os.environ.get(CHANNEL_ENV) or None)
    manager = sync_playwright()
    try:
        pw = manager.start()
    except Exception as exc:  # noqa: BLE001
        raise BrowserUnavailable(f"playwright could not start: {exc}") from exc
    try:
        try:
            launcher = getattr(pw, browser_name)
            options: dict[str, Any] = {"channel": channel} if channel else {}
            if browser_name == "chromium":
                options["args"] = with_chrome_args()
            browser = launcher.launch(**options)
            context = browser.new_context(
                viewport=viewport or {"width": 1280, "height": 800},
                device_scale_factor=device_scale_factor,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrowserUnavailable(f"{browser_name} could not be started: {exc}") from exc
        try:
            yield context
        finally:
            browser.close()
    finally:
        pw.stop()
