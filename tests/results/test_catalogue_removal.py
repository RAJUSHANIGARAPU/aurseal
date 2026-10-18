"""A filtered run never marks cases removed; only a complete catalogue build does."""

from __future__ import annotations

from results.catalogue import build_catalogue, store_catalogue
from tests.results._helpers import rows
from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]

TWO = "def test_keep():\n    pass\n\ndef test_drop():\n    pass\n"


def _status(db_path) -> dict[str, str]:
    return {
        r["case_key"].split("::")[-1]: r["status"]
        for r in rows(db_path, "SELECT case_key, status FROM cases")
    }


def test_filtered_runs_keep_cases_and_a_full_build_removes(pytester, store, db_path):
    pytester.makepyfile(test_two=TWO)
    store_catalogue(store, build_catalogue(pytester.path))
    assert _status(db_path) == {"test_keep": "active", "test_drop": "active"}

    pytester.makepyfile(test_two="def test_keep():\n    pass\n")
    # A -k filtered recorder run only refreshes what it saw.
    pytester.runpytest(*PLUGIN, "-k", "keep")
    assert _status(db_path) == {"test_keep": "active", "test_drop": "active"}

    # A selection argument makes the build partial: still nothing removed.
    partial = build_catalogue(pytester.path, ["-k", "keep"])
    assert not partial.complete
    assert store_catalogue(store, partial) == 0
    assert _status(db_path)["test_drop"] == "active"

    # The complete build decides.
    assert store_catalogue(store, build_catalogue(pytester.path)) == 1
    assert _status(db_path) == {"test_keep": "active", "test_drop": "removed"}


def test_a_collection_error_makes_the_build_partial(pytester, store):
    pytester.makepyfile(test_ok=TWO, test_broken="import nothing_here_at_all\n")
    cat = build_catalogue(pytester.path)
    assert cat.errors and not cat.complete
    assert store_catalogue(store, cat) == 0
