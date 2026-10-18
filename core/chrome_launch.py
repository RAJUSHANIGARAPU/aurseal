"""
Launch switches that keep ``Browser.close()`` from waiting on Google's updater.

Branded Chrome (``--browser-channel=chrome``) wakes ``GoogleUpdater`` about half a
minute after it starts. The updater and its crash handler inherit Chrome's stderr,
which is a pipe to the Playwright driver, and the driver only finishes
``browser.close()`` once every holder of that pipe has closed it. So a browser that
lived longer than ~30s takes as long to close as the updater takes to idle out:
measured 66s for one browser, 110-750s per xdist worker in the demo_ui lane.

``--disable-updater-scheduler`` stops Chrome from waking the updater; close is back
to ~1s. Chromium builds ignore the switch, so it is safe for every chromium launch.
"""

from __future__ import annotations

from collections.abc import Sequence

CHROME_ARGS: tuple[str, ...] = ("--disable-updater-scheduler",)


def with_chrome_args(args: Sequence[str] | None = None) -> list[str]:
    """``args`` plus :data:`CHROME_ARGS`, keeping order and dropping duplicates."""
    merged = list(args or [])
    merged.extend(arg for arg in CHROME_ARGS if arg not in merged)
    return merged
