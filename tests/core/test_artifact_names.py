"""
Artifact filenames, which used to be built inline in a browser-only fixture and
so could not be checked without launching one.

Two faults lived there. Videos were all moved to one path, because the
destination was recomputed from values that do not vary inside the loop — so a
test recording two pages silently kept only the last. And the browser came from
``getoption("--browser")``, which is ``action="append"`` and therefore a list,
putting a literal ``[]`` in every name.

The second class of test below is the one that matters: it is easy to "fix"
collisions by making every name unique, which would renumber the ordinary
single-video case and change every existing report for a situation that mostly
does not arise.
"""

from __future__ import annotations

import pytest

from core.artifact_names import trace_path, video_path

NAME = "test_search"
BROWSER = "chromium"
STAMP = "20260903_101500"


class TestVideosDoNotCollide:
    def test_two_videos_get_different_paths(self):
        first = video_path(NAME, BROWSER, STAMP, 0, 2)
        second = video_path(NAME, BROWSER, STAMP, 1, 2)
        assert first != second

    def test_every_video_in_a_larger_set_is_unique(self):
        total = 5
        paths = [video_path(NAME, BROWSER, STAMP, i, total) for i in range(total)]
        assert len(set(paths)) == total

    def test_the_index_is_one_based_in_the_name(self):
        """Readability, and it means _1 is the first page rather than the second."""
        assert video_path(NAME, BROWSER, STAMP, 0, 2).endswith("_1.webm")
        assert video_path(NAME, BROWSER, STAMP, 1, 2).endswith("_2.webm")


class TestTheOrdinaryCaseIsUnchanged:
    """
    Control. Numbering everything unconditionally would also pass the class
    above while renaming every artifact this project has ever produced.
    """

    def test_a_single_video_is_not_numbered(self):
        assert video_path(NAME, BROWSER, STAMP, 0, 1) == (
            f"reports/videos/{NAME}_{BROWSER}_{STAMP}.webm"
        )

    def test_a_zero_total_is_not_numbered_either(self):
        """Defensive: `total` comes from len(), so 0 should not produce `_1`."""
        assert video_path(NAME, BROWSER, STAMP, 0, 0).endswith(f"{STAMP}.webm")

    def test_the_trace_is_never_numbered(self):
        assert trace_path(NAME, BROWSER, STAMP) == (
            f"reports/traces/{NAME}_{BROWSER}_{STAMP}.zip"
        )


class TestTheBrowserNameIsUsedLiterally:
    @pytest.mark.parametrize("path_fn", [
        lambda b: trace_path(NAME, b, STAMP),
        lambda b: video_path(NAME, b, STAMP, 0, 1),
    ])
    def test_a_list_shaped_string_never_appears(self, path_fn):
        """
        The old code interpolated `--browser`, an append-action option whose
        default is `[]`, so names came out as `test_search_[]_2026...`.
        """
        result = path_fn(BROWSER)
        assert "[" not in result
        assert "]" not in result
        assert f"_{BROWSER}_" in result

    @pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
    def test_each_browser_reaches_the_name(self, browser):
        assert f"_{browser}_" in video_path(NAME, browser, STAMP, 0, 1)

    def test_different_browsers_do_not_collide(self):
        assert video_path(NAME, "chromium", STAMP, 0, 1) != video_path(
            NAME, "firefox", STAMP, 0, 1
        )


class TestPathsLandInTheRightPlace:
    def test_videos_go_to_the_video_directory(self):
        assert video_path(NAME, BROWSER, STAMP, 0, 1).startswith("reports/videos/")

    def test_traces_go_to_the_trace_directory(self):
        assert trace_path(NAME, BROWSER, STAMP).startswith("reports/traces/")

    def test_the_test_name_is_preserved_verbatim(self):
        """Parametrised node ids carry brackets; they must survive into the name."""
        node = "test_login[chromium-nl]"
        assert node in video_path(node, BROWSER, STAMP, 0, 1)
