"""Run compare: every bucket, including a different failure in both runs."""

from __future__ import annotations

import pytest

from results.store import StoreError
from results.trends import compare_runs
from tests.results._history import add_run


def test_every_bucket(store):
    add_run(
        store,
        "base",
        1,
        {
            "goes_red": "passed",
            "goes_green": "failed",
            "same_red": "failed",
            "new_reason": "failed",
            "goes_skip": "passed",
            "removed": "passed",
            "steady": "passed",
        },
        fingerprints={"same_red": "f1", "new_reason": "f1", "goes_green": "f9"},
    )
    add_run(
        store,
        "head",
        2,
        {
            "goes_red": "failed",
            "goes_green": "passed",
            "same_red": "failed",
            "new_reason": "error",
            "goes_skip": "skipped",
            "brand_new": "passed",
            "steady": "passed",
        },
        fingerprints={"same_red": "f1", "new_reason": "f2", "goes_red": "f3"},
        messages={"goes_red": "AssertionError: total was 0"},
    )
    cmp = compare_runs(store, "head")
    assert cmp.baseline_run_id == "base"

    def names(bucket):
        return [n.split("::")[-1] for n in getattr(cmp, bucket)]

    assert names("new_failures") == ["goes_red"]
    assert names("fixed") == ["goes_green"]
    assert names("still_failing") == ["new_reason", "same_red"]
    assert names("fingerprint_changed") == ["new_reason"]
    assert names("newly_skipped") == ["goes_skip"]
    assert names("new_tests") == ["brand_new"]
    assert names("removed_tests") == ["removed"]
    md = cmp.to_markdown()
    assert "for a different reason" in md and "total was 0" in md
    assert cmp.to_json()["counts"]["new_failures"] == 1


def test_baseline_is_the_previous_run_on_the_same_lane_and_branch(store):
    add_run(store, "a1", 1, {"t": "passed"}, lane="offline")
    add_run(store, "b1", 2, {"t": "passed"}, lane="browser")
    add_run(store, "a-other-branch", 3, {"t": "passed"}, lane="offline", branch="feature")
    add_run(store, "a2", 4, {"t": "failed"}, lane="offline")
    assert compare_runs(store, "a2").baseline_run_id == "a1"
    assert compare_runs(store, "a2", "b1").baseline_run_id == "b1"


def test_no_baseline_says_so(store):
    add_run(store, "only", 1, {"t": "passed"})
    cmp = compare_runs(store, "only")
    assert cmp.baseline_run_id is None
    assert "nothing to compare against" in cmp.to_markdown()


def test_unknown_run(store):
    with pytest.raises(StoreError):
        compare_runs(store, "ghost")
