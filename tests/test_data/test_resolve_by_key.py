"""Tests for test_data.parties.select_by_key and LiveResolver.resolve_key: no fallback, ever."""

from __future__ import annotations

import pytest

from test_data.parties import AmbiguousMatch, KeyNotFound, LiveResolver, select_by_key

ROWS = [
    {"id": "1", "reference": "PAR-D1-7", "name": "Seed Partner", "state": "active"},
    {"id": "2", "reference": "PAR-D1-8", "name": "Seed Partner", "state": "active"},
    {"id": "3", "reference": "PAR-D1-9", "name": "Other Partner", "state": "retired"},
    {"id": "4", "reference": "PAR-D1-9", "name": "Other Partner", "state": "active"},
]


def _active(r):
    return r["state"] == "active"


def test_resolves_by_key_even_when_names_collide():
    assert select_by_key(ROWS, "partner", "PAR-D1-8")["id"] == "2"


def test_unselectable_duplicates_do_not_count():
    assert select_by_key(ROWS, "partner", "PAR-D1-9", selectable=_active)["id"] == "4"


def test_missing_key_never_falls_back():
    with pytest.raises(KeyNotFound, match="refresh the pinned key"):
        select_by_key(ROWS, "partner", "PAR-D1-404")


def test_duplicate_key_fails_with_the_ids():
    with pytest.raises(AmbiguousMatch, match=r"\['3', '4'\]"):
        select_by_key(ROWS, "partner", "PAR-D1-9")


def test_blank_key_is_a_caller_error():
    with pytest.raises(ValueError):
        select_by_key(ROWS, "partner", " ")


def test_resolve_key_caches_per_kind_and_key():
    calls = []
    resolver = LiveResolver(lambda kind: calls.append(kind) or ROWS)
    assert resolver.resolve_key("partner", "PAR-D1-7")["id"] == "1"
    resolver.resolve_key("partner", "PAR-D1-7")
    assert calls == ["partner"]
    resolver.resolve_key("partner", "PAR-D1-8")
    assert calls == ["partner", "partner"]


def test_paged_lister_stops_at_the_first_page_with_the_key():
    read: list[int] = []

    def pages(kind):
        for number, page in enumerate([ROWS[:1], ROWS[1:2], ROWS[2:]]):
            read.append(number)
            yield page

    resolver = LiveResolver(
        lambda kind: pytest.fail("the full listing must not be read"), list_pages=pages
    )
    assert resolver.resolve_key("partner", "PAR-D1-8")["id"] == "2"
    assert read == [0, 1], "pages after the one carrying the key must not be fetched"


def test_paged_lister_without_the_key_reads_everything_then_fails():
    resolver = LiveResolver(lambda kind: [], list_pages=lambda kind: iter([ROWS[:2], ROWS[2:]]))
    with pytest.raises(KeyNotFound):
        resolver.resolve_key("partner", "PAR-D1-404")
