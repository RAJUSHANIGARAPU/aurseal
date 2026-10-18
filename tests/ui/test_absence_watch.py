"""AbsenceWatch against a fake page that records init scripts and evaluate calls."""

from __future__ import annotations

import json

import pytest

from ui.components import absence_watch as aw
from ui.components.absence_watch import AbsenceWatch


class FakePage:
    """Answers the watch's three page queries from plain attributes."""

    def __init__(self, *, stored=None, live=True, arm_error=None, query_error=None):
        self.calls: list[tuple[str, object]] = []
        self.init_scripts: list[str] = []
        self.stored = stored
        self.live = live
        self.arm_error = arm_error
        self.query_error = query_error

    def add_init_script(self, script=None, path=None):
        self.calls.append(("init", script))
        self.init_scripts.append(script)

    def evaluate(self, expression, arg=None):
        if expression == aw._CLEAR:
            self.calls.append(("clear", arg))
            self.stored = None
            return None
        if self.query_error and expression in (aw._LIVE, aw._READ):
            raise self.query_error
        if expression == aw._LIVE:
            return self.live
        if expression == aw._READ:
            return None if self.stored is None else json.dumps(self.stored)
        self.calls.append(("arm", expression))
        if self.arm_error:
            raise self.arm_error
        return None


def test_no_phrases_is_a_value_error():
    with pytest.raises(ValueError):
        AbsenceWatch(FakePage(), [])


def test_install_registers_an_every_document_script_and_arms_the_current_one():
    page = FakePage()
    phrase = 'Confirm "early" end'
    AbsenceWatch(page, [phrase, "Done"]).install()
    assert len(page.init_scripts) == 1
    script = page.init_scripts[0]
    assert json.dumps([phrase, "Done"]) in script  # quotes escaped as JSON
    armed = [c for c in page.calls if c[0] == "arm"]
    assert armed == [("arm", script)]


def test_install_clears_earlier_sightings_before_arming():
    page = FakePage(stored=["Old"])
    AbsenceWatch(page, ["Old"]).install()
    kinds = [kind for kind, _ in page.calls]
    assert kinds.index("clear") < kinds.index("init") < kinds.index("arm")
    assert page.stored is None


def test_install_tolerates_a_failure_arming_the_current_document(caplog):
    page = FakePage(arm_error=RuntimeError("page closed"))
    watch = AbsenceWatch(page, ["X"]).install()
    assert page.init_scripts  # future documents are still armed
    assert watch is not None


def test_absence_and_positive_control_pass_when_live():
    watch = AbsenceWatch(FakePage(stored=["Shown"]), ["Shown", "Forbidden"])
    watch.assert_never_seen("Forbidden", "the user must not be offered this")
    watch.assert_seen("Shown", "the flow shows it")
    assert watch.was_seen("Shown") and not watch.was_seen("Forbidden")


def test_absence_fails_naming_phrase_and_reason():
    watch = AbsenceWatch(FakePage(stored=["Forbidden"]), ["Forbidden"])
    with pytest.raises(AssertionError) as err:
        watch.assert_never_seen("Forbidden", "refunds are not offered here")
    assert "Forbidden" in str(err.value) and "refunds are not offered here" in str(err.value)


def test_positive_control_fails_and_invalidates_the_absence_verdict():
    watch = AbsenceWatch(FakePage(stored=[]), ["Shown"])
    with pytest.raises(AssertionError, match="absence verdict from this watch is invalid"):
        watch.assert_seen("Shown", "the flow shows it")


@pytest.mark.parametrize("method", ["assert_seen", "assert_never_seen"])
def test_both_assertions_fail_as_a_harness_problem_when_not_live(method):
    watch = AbsenceWatch(FakePage(stored=["X"], live=False), ["X"])
    with pytest.raises(AssertionError, match="test-harness problem"):
        getattr(watch, method)("X", "why")


def test_a_page_that_cannot_be_queried():
    watch = AbsenceWatch(FakePage(query_error=RuntimeError("target closed")), ["X"])
    assert watch.seen() == []
    with pytest.raises(AssertionError, match="unreliable") as err:
        watch.assert_live("why")
    assert isinstance(err.value.__cause__, RuntimeError)


def test_non_strings_and_corrupt_values_are_ignored():
    assert AbsenceWatch(FakePage(stored=["A", 3, None, {"x": 1}, "B"]), ["A"]).seen() == ["A", "B"]
    page = FakePage()
    page.evaluate = lambda expr, arg=None: "{not json" if expr == aw._READ else True
    assert AbsenceWatch(page, ["A"]).seen() == []


def test_asserting_an_unwatched_phrase_is_a_usage_error():
    watch = AbsenceWatch(FakePage(stored=[]), ["A"])
    with pytest.raises(ValueError):
        watch.assert_never_seen("B", "B was never watched, so absence would prove nothing")


def test_each_watch_has_its_own_storage_key():
    first, second = AbsenceWatch(FakePage(), ["A"]), AbsenceWatch(FakePage(), ["A"])
    assert first.key != second.key
