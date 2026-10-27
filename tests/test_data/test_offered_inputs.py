"""Tests for inputs drawn from what the system offers: choose_other and require_offered."""

from __future__ import annotations

import pytest

from test_data.codelists import require_offered
from ui.components.dropdown import NothingToChange, choose_other
from verification.evidence_verdict import HarnessError

OFFERED = ["Monthly", "Quarterly", "Yearly"]


class TestChooseOther:
    def test_preferred_when_offered_and_different(self):
        assert choose_other("Monthly", "Yearly", OFFERED) == "Yearly"

    def test_preferred_equal_to_current_falls_to_the_first_different(self):
        assert choose_other("Yearly", "Yearly", OFFERED) == "Monthly"

    def test_preferred_not_offered_falls_to_the_first_different(self):
        assert choose_other("Monthly", "Weekly", OFFERED) == "Quarterly"

    def test_no_current_value_takes_the_preferred(self):
        assert choose_other(None, "Quarterly", OFFERED) == "Quarterly"

    def test_custom_equality(self):
        def same(a, b):
            return (a or "").strip().lower() == b.strip().lower()

        assert choose_other(" monthly ", "Monthly", OFFERED, same=same) == "Quarterly"

    def test_nothing_different_raises(self):
        with pytest.raises(NothingToChange, match="Monthly"):
            choose_other("Monthly", None, ["Monthly"])
        with pytest.raises(NothingToChange):
            choose_other("Monthly", "Monthly", [])


class TestRequireOffered:
    CODES = ["No longer needed", "Budget", "Consolidating", "Other"]

    def test_offered_label_passes_through_trimmed(self):
        assert require_offered(" Budget ", self.CODES) == "Budget"

    def test_stale_label_is_a_harness_error_naming_label_and_list(self):
        with pytest.raises(HarnessError) as caught:
            require_offered("Too expensive", self.CODES)
        message = str(caught.value)
        assert "'Too expensive'" in message and "Consolidating" in message
        assert not isinstance(caught.value, AssertionError), "not a product failure"

    def test_case_matters(self):
        with pytest.raises(HarnessError):
            require_offered("budget", self.CODES)

    def test_empty_list_and_blank_label(self):
        with pytest.raises(HarnessError, match="empty"):
            require_offered("Budget", [])
        with pytest.raises(HarnessError):
            require_offered("  ", self.CODES)
