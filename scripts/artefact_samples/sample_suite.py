"""The fixed demo run behind docs/artefact-samples/: one pass, one failure, one skip and one
expected-red detector, all against the in-repo demo service's code (no network, no browser).

Not collected by the suite (no ``test_`` file prefix); ``python -m reporting.artefact_samples``
runs it by path.
"""

from __future__ import annotations

import pytest

from demo_sut.documents import render_lines


@pytest.mark.requirement("REQ-1")
def test_a_letter_renders_its_greeting():
    lines = render_lines("Dear {{customer.name}},", {"customer": {"name": "Ada"}})
    assert lines == ["Dear Ada,"]


def test_a_failing_expectation():
    lines = render_lines("Total: {{total}}", {"total": "10.00"})
    assert lines == ["Total: 12.00"], "the sample run needs one real failure"


def test_skipped_without_a_target():
    pytest.skip("no target configured for this sample")


@pytest.mark.known_defect(
    "BUG-7",
    reason="the sample planted defect: the purpose line is dropped",
    raises=AssertionError,
    match=r"purpose line missing",
)
def test_purpose_line_is_present():
    # A plain local, so the assertion message carries no object repr (no memory address).
    text = " ".join(render_lines("Hello", {}))
    assert "Purpose" in text, "BUG-7: purpose line missing"
