"""Tests for autonomous_ui.flakiness.reporter."""

from __future__ import annotations

import json

import pytest

from autonomous_ui.flakiness.models import (
    FlakinessProfile,
    FlakPattern,
    RemediationResult,
)
from autonomous_ui.flakiness.reporter import FlakinessReporter


def _profile(
    test_id: str = "test_flaky", rate: float = 0.25, is_flaky: bool = True
) -> FlakinessProfile:
    return FlakinessProfile(
        test_id=test_id,
        total_runs=20,
        failure_count=int(rate * 20),
        flakiness_rate=rate,
        confidence=1.0,
        is_flaky=is_flaky,
        most_common_error="TimeoutError: 30000ms",
        avg_duration_s=2.5,
        last_failure_ts="2026-04-25T10:00:00Z",
        max_consecutive_failures=3,
    )


def _remediation(test_id: str = "test_flaky") -> RemediationResult:
    return RemediationResult(
        test_id=test_id,
        pattern=FlakPattern.TIMING,
        strategy="add_explicit_wait",
        suggestion="Add page.wait_for_selector() before the interaction.",
        auto_applied=False,
    )


@pytest.fixture()
def reporter() -> FlakinessReporter:
    return FlakinessReporter()


@pytest.fixture()
def flaky_profiles() -> list[FlakinessProfile]:
    return [_profile("test_search"), _profile("test_login", rate=0.10)]


@pytest.fixture()
def analyses(flaky_profiles) -> dict:
    return {p.test_id: (FlakPattern.TIMING, _remediation(p.test_id)) for p in flaky_profiles}


# ------------------------------------------------------------------
# generate_markdown()
# ------------------------------------------------------------------


def test_markdown_contains_summary(reporter, flaky_profiles, analyses) -> None:
    md = reporter.generate_markdown(flaky_profiles, analyses)
    assert "## Summary" in md
    assert "Flaky tests detected: 2" in md


def test_markdown_table_lists_flaky_tests(reporter, flaky_profiles, analyses) -> None:
    md = reporter.generate_markdown(flaky_profiles, analyses)
    assert "test_search" in md
    assert "test_login" in md


def test_markdown_recommendations_section(reporter, flaky_profiles, analyses) -> None:
    md = reporter.generate_markdown(flaky_profiles, analyses)
    assert "## Recommendations" in md
    assert "add_explicit_wait" in md


def test_markdown_no_flaky_message_when_empty(reporter) -> None:
    stable = [_profile("test_stable", is_flaky=False)]
    md = reporter.generate_markdown(stable, {})
    assert "No flaky tests detected" in md


def test_markdown_includes_run_timestamp(reporter, flaky_profiles, analyses) -> None:
    md = reporter.generate_markdown(flaky_profiles, analyses, run_ts="2026-04-25 10:00 UTC")
    assert "2026-04-25 10:00 UTC" in md


# ------------------------------------------------------------------
# generate_json()
# ------------------------------------------------------------------


def test_json_summary_counts(reporter, flaky_profiles, analyses) -> None:
    result = reporter.generate_json(flaky_profiles, analyses)
    assert result["summary"]["total_tracked"] == 2
    assert result["summary"]["flaky_count"] == 2
    assert result["summary"]["stable_count"] == 0


def test_json_flaky_tests_have_required_fields(reporter, flaky_profiles, analyses) -> None:
    result = reporter.generate_json(flaky_profiles, analyses)
    for item in result["flaky_tests"]:
        assert "test_id" in item
        assert "flakiness_rate" in item
        assert "severity" in item
        assert "pattern" in item
        assert "strategy" in item


def test_json_excludes_stable_tests(reporter) -> None:
    profiles = [_profile("test_stable", is_flaky=False)]
    result = reporter.generate_json(profiles, {})
    assert result["flaky_tests"] == []


# ------------------------------------------------------------------
# write()
# ------------------------------------------------------------------


def test_write_creates_both_files(reporter, tmp_path, flaky_profiles, analyses) -> None:
    md_path, json_path = reporter.write(flaky_profiles, analyses, output_dir=tmp_path)
    assert md_path.exists()
    assert json_path.exists()


def test_write_json_is_valid(reporter, tmp_path, flaky_profiles, analyses) -> None:
    _, json_path = reporter.write(flaky_profiles, analyses, output_dir=tmp_path)
    parsed = json.loads(json_path.read_text())
    assert "summary" in parsed
    assert "flaky_tests" in parsed


# ------------------------------------------------------------------
# Bounded report directory
# ------------------------------------------------------------------


def test_write_prunes_old_report_pairs(reporter, tmp_path, flaky_profiles, analyses) -> None:
    # Live in the source repo when this was found: reports/flakiness/ held 664
    # files, roughly one .md/.json pair per invocation once anything is flaky.
    for i in range(10):
        (tmp_path / f"report-2026010{i}T000000Z.md").write_text("old")
        (tmp_path / f"report-2026010{i}T000000Z.json").write_text("{}")

    md_path, _ = reporter.write(flaky_profiles, analyses, output_dir=tmp_path, max_pairs=3)

    remaining = sorted(p.name for p in tmp_path.glob("report-*"))
    assert len(remaining) == 6  # 3 pairs
    assert md_path.name in remaining


def test_write_keeps_the_newest_reports(reporter, tmp_path, flaky_profiles, analyses) -> None:
    for i in range(5):
        (tmp_path / f"report-2026010{i}T000000Z.md").write_text("old")
        (tmp_path / f"report-2026010{i}T000000Z.json").write_text("{}")

    reporter.write(flaky_profiles, analyses, output_dir=tmp_path, max_pairs=2)

    stems = sorted({p.stem for p in tmp_path.glob("report-*")})
    assert "report-20260100T000000Z" not in stems  # oldest went first


def test_write_below_the_cap_deletes_nothing(reporter, tmp_path, flaky_profiles, analyses) -> None:
    # Positive control: rotation must not eat a directory that is within budget.
    (tmp_path / "report-20260101T000000Z.md").write_text("old")
    (tmp_path / "report-20260101T000000Z.json").write_text("{}")

    reporter.write(flaky_profiles, analyses, output_dir=tmp_path, max_pairs=5)

    assert (tmp_path / "report-20260101T000000Z.md").exists()
    assert len(list(tmp_path.glob("report-*"))) == 4
