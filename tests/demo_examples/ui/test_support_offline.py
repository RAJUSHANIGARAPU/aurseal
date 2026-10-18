"""Offline checks of the example helpers (no browser, so no ``demo_ui`` marker)."""

from __future__ import annotations

from tests.demo_examples.ui import support


def test_first_complete_skips_half_filled_candidates():
    candidates = [
        ("submitted", {"account": None, "holder": None}),
        ("subscription", {"account": "XA00P", "holder": "  "}),
        ("customer", {"account": "XA00Q", "holder": "Offline Co"}),
    ]
    assert support.first_complete(candidates) == (
        "customer",
        {"account": "XA00Q", "holder": "Offline Co"},
    )
    assert support.first_complete([("a", None), ("b", {"account": "x"})]) is None


def test_segments_pair_holder_type_and_plan():
    assert support.SEGMENTS["company"] == {"type": "COMPANY", "plan": "TEAM"}
    assert support.SEGMENTS["individual"] == {"type": "INDIVIDUAL", "plan": "INDIVIDUAL"}
