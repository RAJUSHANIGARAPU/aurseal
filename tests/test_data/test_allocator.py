"""Tests for test_data.allocator: the cross-worker claim ledger and target resolution."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from test_data.allocator import (
    PAGE_LIMIT_KEY,
    ListingPage,
    PreflightResult,
    TargetPicker,
    claim_for,
    max_pages_from_env,
    resolve_targets,
)


def _alloc(tmp_path: Path, run: str = "run-1") -> TargetPicker:
    return TargetPicker(run, ledger_root=tmp_path / "ledger", log_path=tmp_path / "t.log")


def _race(args: tuple[str, str, str]) -> bool:
    root, ident, test = args
    a = TargetPicker("race", ledger_root=Path(root), log_path=None)
    return a.claim(ident, test)


class TestTargetAllocator:
    def test_claim_once_then_refused(self, tmp_path):
        a = _alloc(tmp_path)
        assert a.is_available("ORD-1")
        assert a.claim("ORD-1", "test_a", "ref v7")
        assert not a.claim("ORD-1", "test_b")
        assert not a.is_available("ORD-1")
        assert a.claims() == {"ORD-1": "test_a"}

    def test_claims_are_shared_between_instances_of_one_run(self, tmp_path):
        assert _alloc(tmp_path).claim("ORD-1", "test_a")
        other_worker = _alloc(tmp_path)
        assert not other_worker.claim("ORD-1", "test_b")
        assert other_worker.claims() == {"ORD-1": "test_a"}

    def test_runs_are_isolated(self, tmp_path):
        assert _alloc(tmp_path, "run-1").claim("ORD-1", "t")
        assert _alloc(tmp_path, "run-2").claim("ORD-1", "t")

    def test_rejected_entity_is_never_claimed(self, tmp_path):
        a = _alloc(tmp_path)
        a.reject("ORD-2", "open draft")
        a.reject("ORD-2", "second reason is ignored")
        assert not a.claim("ORD-2", "t")
        assert a.rejections() == {"ORD-2": "open draft"}

    def test_identifiers_with_odd_characters_get_distinct_files(self, tmp_path):
        a = _alloc(tmp_path)
        assert a.claim("A/B", "t1")
        assert a.claim("A_B", "t2")
        assert a.claims() == {"A/B": "t1", "A_B": "t2"}

    def test_log_records_every_claim_and_rejection(self, tmp_path):
        a = _alloc(tmp_path)
        a.claim("ORD-1", "test_a", "ref v7")
        a.reject("ORD-2", "locked")
        lines = (tmp_path / "t.log").read_text().splitlines()
        assert len(lines) == 2
        assert "run=run-1 CLAIMED ORD-1 by test_a - ref v7" in lines[0]
        assert "REJECTED ORD-2 - locked" in lines[1]

    def test_status_explains_every_identifier(self, tmp_path):
        a = _alloc(tmp_path)
        a.claim("ORD-1", "test_a")
        a.reject("ORD-2", "locked")
        status = a.status(["ORD-1", "ORD-2", "ORD-3", "ORD-4"], {"ORD-3": "not found"})
        assert status == (
            "ORD-1 claimed by test_a; ORD-2 rejected (locked); ORD-3 not found; ORD-4 available"
        )

    def test_default_run_id_comes_from_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_RUN_ID", "nightly 7")
        a = TargetPicker(ledger_root=tmp_path, log_path=None)
        assert a.run_id == "nightly-7"
        assert a.directory == tmp_path / "nightly-7"

    def test_only_one_process_wins_a_race(self, tmp_path):
        args = [(str(tmp_path), "ORD-9", f"test_{n}") for n in range(8)]
        with ProcessPoolExecutor(max_workers=4) as pool:
            wins = list(pool.map(_race, args))
        assert wins.count(True) == 1


def _pages(*pages: list[tuple[str, str]], last_at: int | None = None):
    calls = []

    def lookup(page: int):
        calls.append(page)
        if page >= len(pages):
            return ListingPage([])
        return ListingPage(pages[page], last=page == last_at)

    return lookup, calls


class TestResolveTargets:
    def test_stops_once_everything_is_found(self):
        lookup, calls = _pages([("A", "a1"), ("B", "b1")], [("C", "c1")])
        assert resolve_targets(lookup, ["B"]) == {"B": "b1"}
        assert calls == [0]

    def test_reads_further_pages_until_found(self):
        lookup, calls = _pages([("A", "a1")], [("C", "c1")])
        assert resolve_targets(lookup, ["C", "Z"]) == {"C": "c1"}
        assert calls == [0, 1, 2]

    def test_last_flag_ends_the_listing(self):
        lookup, calls = _pages([("A", "a1")], [("C", "c1")], last_at=0)
        assert resolve_targets(lookup, ["C"]) == {}
        assert calls == [0]

    def test_max_pages_caps_reads(self):
        lookup, calls = _pages([("A", "a")], [("B", "b")], [("C", "c")])
        assert resolve_targets(lookup, ["C"], max_pages=2) == {}
        assert calls == [0, 1]

    def test_unreadable_page_stops(self):
        assert resolve_targets(lambda page: None, ["A"]) == {}

    def test_listing_that_ignores_the_page_parameter_stops(self):
        calls = []

        def lookup(page):
            calls.append(page)
            return ListingPage([("A", "a1")])

        assert resolve_targets(lookup, ["Z"]) == {}
        assert calls == [0, 1]

    def test_max_pages_from_env(self):
        assert max_pages_from_env({}) == 20
        assert max_pages_from_env({PAGE_LIMIT_KEY: "3"}) == 3
        with pytest.raises(ValueError):
            max_pages_from_env({PAGE_LIMIT_KEY: "0"})


class TestClaimFor:
    LISTING = [("ORD-1", "v1"), ("ORD-2", "v2"), ("ORD-3", "v3")]

    def _lookup(self, page):
        return ListingPage(self.LISTING, last=True) if page == 0 else None

    def test_first_suitable_candidate_is_claimed(self, tmp_path):
        a = _alloc(tmp_path)
        out = claim_for(
            a,
            ["ORD-1", "ORD-2"],
            "test_a",
            lookup_page=self._lookup,
            preflight=lambda i, r: PreflightResult(True),
        )
        assert out.claim and out.claim.ident == "ORD-1" and out.claim.reference == "v1"
        out2 = claim_for(
            a,
            ["ORD-1", "ORD-2"],
            "test_b",
            lookup_page=self._lookup,
            preflight=lambda i, r: PreflightResult(True),
        )
        assert out2.claim and out2.claim.ident == "ORD-2"

    def test_unsafe_candidates_are_rejected_for_the_run(self, tmp_path):
        a = _alloc(tmp_path)

        def preflight(ident, ref):
            if ident == "ORD-1":
                return PreflightResult(False, "corrupt period", unsafe=True)
            return PreflightResult(False, "no open period")

        out = claim_for(
            a,
            ["ORD-1", "ORD-2", "ORD-404"],
            "test_a",
            lookup_page=self._lookup,
            preflight=preflight,
        )
        assert out.claim is None
        assert out.explanation == (
            "ORD-1 rejected (corrupt period); "
            "ORD-2 unsuitable for test_a: no open period; "
            "ORD-404 not found in the listing"
        )
        assert a.rejections() == {"ORD-1": "corrupt period"}
        assert a.is_available("ORD-2")

    def test_exhausted_allowlist_explains_without_listing(self, tmp_path):
        a = _alloc(tmp_path)
        a.claim("ORD-1", "test_x")
        out = claim_for(
            a,
            ["ORD-1"],
            "test_a",
            lookup_page=lambda p: pytest.fail("must not list"),
            preflight=lambda i, r: PreflightResult(True),
        )
        assert out.claim is None and out.explanation == "ORD-1 claimed by test_x"

    def test_concurrent_claim_moves_on(self, tmp_path):
        a = _alloc(tmp_path)
        rival = _alloc(tmp_path)

        def preflight(ident, ref):
            if ident == "ORD-1":
                rival.claim("ORD-1", "test_rival")  # another worker wins meanwhile
            return PreflightResult(True)

        out = claim_for(
            a, ["ORD-1", "ORD-2"], "test_a", lookup_page=self._lookup, preflight=preflight
        )
        assert out.claim and out.claim.ident == "ORD-2"
