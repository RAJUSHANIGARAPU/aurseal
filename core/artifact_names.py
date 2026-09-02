"""
Naming the trace and video files a test run leaves behind.

This lived inline in the artifact fixture, where nothing could reach it, and it
had two faults that only show up on disk.

The destination was computed from values that do not change inside the loop that
moves the videos — test name, browser, timestamp — so every page's recording was
moved to the *same* path. Any test that opens a second page (a popup, a
``target=_blank``, a second ``new_page()``) silently kept only the last video and
overwrote the rest. ``record_video_dir`` is set for the whole context, so every
page records and the loop really does run more than once.

The browser came from ``request.config.getoption("--browser")``, which
pytest-playwright declares as ``action="append"`` with a default of ``[]``. It is
a list, not a string, so the filenames carried a literal ``[]`` — or
``['chromium']`` — where the browser name was meant to go.

Both are just string construction, so they belong in a function that can be
tested without a browser.
"""

from __future__ import annotations

TRACE_DIR = "reports/traces"
VIDEO_DIR = "reports/videos"


def _stem(test_name: str, browser: str, timestamp: str) -> str:
    return f"{test_name}_{browser}_{timestamp}"


def trace_path(test_name: str, browser: str, timestamp: str) -> str:
    """Destination for a test's trace archive. One per test, so no index."""
    return f"{TRACE_DIR}/{_stem(test_name, browser, timestamp)}.zip"


def video_path(test_name: str, browser: str, timestamp: str, index: int, total: int) -> str:
    """
    Destination for one page's recording.

    Numbered only when a test recorded more than one page, so the ordinary
    single-page case keeps the name it has always had and existing reports do
    not all change shape for a case that mostly does not occur.
    """
    suffix = "" if total <= 1 else f"_{index + 1}"
    return f"{VIDEO_DIR}/{_stem(test_name, browser, timestamp)}{suffix}.webm"
