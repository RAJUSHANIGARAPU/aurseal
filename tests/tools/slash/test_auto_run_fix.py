"""auto-run-fix: the loop's stopping rules, the diff guard, and what the command file promises."""

from __future__ import annotations

import pytest

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import diff_guard, stability
from tools.slash.junit import aggregate
from tools.slash.stability import DONE, FIX, GIVE_UP, RERUN, StabilityLoop

PASSING = """<testsuite><testcase classname="t" name="a" time="0.1"/></testsuite>"""
FAILING = """<testsuite><testcase classname="t" name="a" time="0.1">
<failure message="TimeoutError: locator('Save')">trace</failure></testcase></testsuite>"""


# ------------------------------------------------------------------ the loop


def test_green_on_the_first_try_ends_after_exactly_three_runs_with_no_fix_rounds():
    loop = StabilityLoop()
    steps = [loop.record(True) for _ in range(3)]
    assert steps == [RERUN, RERUN, DONE]
    assert loop.runs == 3 and loop.fix_rounds == 0
    with pytest.raises(RuntimeError):
        loop.record(True)


@pytest.mark.parametrize("greens_before", [1, 2])
def test_a_red_run_resets_the_green_count(greens_before):
    loop = StabilityLoop()
    for _ in range(greens_before):
        loop.record(True)
    assert loop.record(False, "E1") == FIX
    assert loop.greens == 0
    assert [loop.record(True) for _ in range(3)] == [RERUN, RERUN, DONE]


def test_never_more_than_five_fix_rounds():
    loop = StabilityLoop()
    steps = [loop.record(False, f"error {i}") for i in range(10) if not loop.finished]
    assert steps.count(FIX) == 5 and steps[-1] == GIVE_UP
    assert loop.fix_rounds == 5


def test_the_same_error_three_rounds_running_stops_early():
    loop = StabilityLoop()
    assert [loop.record(False, "same") for _ in range(3)] == [FIX, FIX, GIVE_UP]
    assert loop.fix_rounds == 2


def test_a_run_with_no_tests_is_not_green(tmp_path):
    empty = tmp_path / "empty.xml"
    empty.write_text("<testsuite/>", encoding="utf-8")
    state = tmp_path / "state.json"
    stability.main(["--state", str(state), "reset"])
    assert stability.main(["--state", str(state), "record", "--junit", str(empty)]) == 0
    assert stability.load(state).fix_rounds == 1


def test_the_command_line_drives_the_loop_from_junit_files(tmp_path, capsys):
    good, bad = tmp_path / "good.xml", tmp_path / "bad.xml"
    good.write_text(PASSING, encoding="utf-8")
    bad.write_text(FAILING, encoding="utf-8")
    state = tmp_path / "state.json"
    stability.main(["--state", str(state), "reset"])
    for _ in range(2):
        stability.main(["--state", str(state), "record", "--junit", str(bad)])
    assert stability.main(["--state", str(state), "record", "--junit", str(bad)]) == 3
    assert "give-up" in capsys.readouterr().out
    assert aggregate([bad]).signature() == "t::a: TimeoutError: locator('Save')"


# ----------------------------------------------------------------- diff guard

PAGE_FIX = """diff --git a/ui/pages/home_page.py b/ui/pages/home_page.py
--- a/ui/pages/home_page.py
+++ b/ui/pages/home_page.py
@@ -1,3 +1,4 @@
-        self.page.locator(".btn-primary").click()
+        self.resolve("save_button").wait_for(state="visible")
+        self.resolve("save_button").click()
"""

ASSERT_EDIT = """--- a/ui/pages/home_page.py
+++ b/ui/pages/home_page.py
@@ -1 +1 @@
-        expect(self.resolve("total")).to_have_text("10")
+        expect(self.resolve("total")).to_have_text("11")
"""

TEST_EDIT = """--- a/ui/tests/test_search.py
+++ b/ui/tests/test_search.py
@@ -1 +1 @@
-    keyword = data["valid_search"]
+    keyword = data["other_search"]
"""

DATA_EDIT = """--- a/ui/testdata/test_data.json
+++ b/ui/testdata/test_data.json
@@ -1 +1 @@
-  "valid_search": "lamp"
+  "valid_search": "chair"
"""


def test_a_selector_and_wait_fix_passes_the_guard():
    assert diff_guard.violations(PAGE_FIX) == []


@pytest.mark.parametrize("diff", [ASSERT_EDIT, TEST_EDIT, DATA_EDIT])
def test_assertions_tests_and_test_data_are_refused(diff):
    assert diff_guard.violations(diff)


def test_a_plain_assert_line_is_refused_anywhere():
    diff = (
        "--- a/core/x.py\n+++ b/core/x.py\n@@ -1 +1 @@\n"
        "-    assert total == 3\n+    assert total == 4\n"
    )
    assert any("assertion" in v for v in diff_guard.violations(diff))


# --------------------------------------------------------------- command file


def test_the_command_is_manual_only_and_listed():
    meta, _ = read_command("auto-run-fix")
    assert meta["disable-model-invocation"] is True
    assert meta["description"]
    assert claude_md_lists("auto-run-fix")


def test_the_command_states_its_rules():
    _, body = read_command("auto-run-fix")
    for promise in (
        "3 consecutive green runs",
        "at most 5 fix rounds",
        "same error survives 3 rounds",
        "Never change assertions, expected values",
        "No fixed sleeps",
        "Each fix is its own commit",
        "Nothing is committed while the run is red",
        "tools.slash.stability",
        "tools.slash.diff_guard",
        "docs/ai_learnings.md",
        "every file changed",
        "show-trace",
    ):
        assert promise in body, promise
