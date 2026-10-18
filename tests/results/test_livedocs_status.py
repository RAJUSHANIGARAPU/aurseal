"""Page status matrix: proven, failing, unproven, stale; all-skip is never proven."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from results.livedocs import page_status, parse_page
from results.models import VerdictRecord
from tests.results._livedocs_fixture import run, seed_cases, write_docs

pytestmark = pytest.mark.feature("living-documentation")

NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _page(tmp_path):
    return parse_page(write_docs(tmp_path) / "billing.md")


def test_proven(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 5, {"pay": "passed", "list": "passed"})
    status = page_status(store, _page(tmp_path), now=NOW)
    assert status.status == "proven"


def test_failing(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 5, {"pay": "failed", "list": "passed"})
    status = page_status(store, _page(tmp_path), now=NOW)
    assert status.status == "failing" and "Pay an invoice: failed" in status.reasons


def test_all_skip_is_never_proven(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 5, {"pay": "skipped", "list": "skipped"})
    assert page_status(store, _page(tmp_path), now=NOW).status == "unproven"


def test_never_executed_and_no_cases_are_unproven(store, tmp_path):
    assert page_status(store, _page(tmp_path), now=NOW).reasons == (
        "no test case is linked to this page",
    )
    seed_cases(store)
    status = page_status(store, _page(tmp_path), now=NOW)
    assert status.status == "unproven" and "never executed" in status.reasons[0]


def test_stale(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 1, {"pay": "passed", "list": "passed"})
    late = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert page_status(store, _page(tmp_path), now=late).status == "stale"
    assert page_status(store, _page(tmp_path), now=late, stale_days=90).status == "proven"


def test_an_unsound_latest_run_is_ignored(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 5, {"pay": "passed", "list": "passed"})
    run(store, "r2", 6, {"pay": "failed", "list": "skipped"}, verdict="FAIL")
    assert page_status(store, _page(tmp_path), now=NOW).status == "proven"


def test_coverage_verdicts_must_include_proved(store, tmp_path):
    seed_cases(store)
    run(store, "r1", 5, {"pay": "passed", "list": "passed"})
    store.add_verdicts(
        [VerdictRecord("r1", "t.py::test_pay", "coverage", "recorded", subject="REQ-12")]
    )
    assert page_status(store, _page(tmp_path), now=NOW).status == "unproven"
    store.add_verdicts(
        [
            VerdictRecord(
                "r1",
                "t.py::test_pay",
                "coverage",
                "proved",
                subject="REQ-12",
                recorded_at="2026-01-05T10:00:01Z",
            )
        ]
    )
    assert page_status(store, _page(tmp_path), now=NOW).status == "proven"
