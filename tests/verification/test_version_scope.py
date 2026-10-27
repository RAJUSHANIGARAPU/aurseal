"""Operator-stated build versions, the version switch, and the published-document fingerprint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.build_info import (
    BuildInfo,
    DocumentFingerprint,
    Match,
    json_pointer,
    stamp,
    stated_or_measured,
)
from verification.evidence_verdict import EvidenceVerdict
from verification.fix_scope import by_version, fingerprint_verdict, judge_by_version
from verification.verdicts import RedVerdict, detail_prefix, read_verdicts, record_verdict

ROOT = Path(__file__).resolve().parents[2]
SIGNATURE = r"BUG-7: proration ignores the seat change date"
UNKNOWN = BuildInfo.unknown("/version -> HTTP 404")


def broken() -> None:
    raise AssertionError("BUG-7: proration ignores the seat change date")


def fine() -> None:
    return None


# --- stated or measured -------------------------------------------------------------


def test_measured_wins_over_stated():
    measured = BuildInfo.measured("version endpoint", "2.4.1")
    assert stated_or_measured(measured, environ={"STATED_BUILD_VERSION": "9.9.9"}) is measured


def test_stated_when_nothing_measured():
    build = stated_or_measured(UNKNOWN, environ={"STATED_BUILD_VERSION": " v2.4.0 "})
    assert build.stated and build.value == "v2.4.0" and build.source == "stated"
    assert stamp(build, "text") == "[version v2.4.0 stated, not measured] text"


def test_neither_stays_unknown():
    assert not stated_or_measured(UNKNOWN, environ={}).known
    assert stamp(BuildInfo.measured("x", "1.0.0"), "text") == "text"


# --- by_version -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deployed", "fixed_in", "expected"),
    [
        ("2.4.0", "2.4.0", True),
        ("v2.10.0", "2.9.3", True),
        ("2.4", "2.4.1", False),
        ("2.4.1-rc.1", "2.4.1", False),
        ("2.4.1+build.7", "v2.4.1", True),
        ("1.0.0-rc.2", "1.0.0-rc.10", False),
    ],
)
def test_by_version_compares_semantic_versions(deployed, fixed_in, expected):
    assert by_version(BuildInfo.measured("probe", deployed), fixed_in) is expected


def test_by_version_unknown_abstains_and_malformed_raises():
    assert by_version(UNKNOWN, "2.4.0") is None
    with pytest.raises(ValueError, match="not a semantic version"):
        by_version(BuildInfo.measured("probe", "2.4.0"), "next-release")
    with pytest.raises(ValueError):
        by_version(BuildInfo.measured("probe", "build 2024-11"), "2.4.0")


# --- judge_by_version -------------------------------------------------------------------


def test_at_or_above_the_fix_the_oracle_is_asserted_and_stamped_when_stated():
    build = stated_or_measured(UNKNOWN, environ={"STATED_BUILD_VERSION": "2.5.0"})
    verdict = judge_by_version(
        build,
        "2.4.0",
        "BUG-7",
        SIGNATURE,
        coverage=EvidenceVerdict("seat proration"),
        control=fine,
        oracle=fine,
        evidence=lambda: "prorated 12 of 30 days",
    )
    assert verdict is RedVerdict.CLEARED
    details = [row["detail"] for row in read_verdicts()]
    assert details and all(d.startswith("[version 2.5.0 stated, not measured]") for d in details)
    assert all(d.count("[version") == 1 for d in details)


def test_below_the_fix_the_oracle_runs_as_a_detector():
    build = BuildInfo.measured("version endpoint", "2.3.9")
    verdict = judge_by_version(
        build,
        "2.4.0",
        "BUG-7",
        SIGNATURE,
        coverage=EvidenceVerdict("seat proration"),
        control=fine,
        oracle=broken,
        evidence=lambda: "unused",
    )
    assert verdict is RedVerdict.REPRODUCES
    assert not read_verdicts()[0]["detail"].startswith("[version")


def test_a_stated_failure_message_carries_the_stamp():
    build = stated_or_measured(UNKNOWN, environ={"STATED_BUILD_VERSION": "2.4.0"})
    with pytest.raises(AssertionError, match=r"^\[version 2\.4\.0 stated, not measured\] BUG-7"):
        judge_by_version(
            build,
            "2.4.0",
            "BUG-7",
            SIGNATURE,
            coverage=EvidenceVerdict("seat proration"),
            control=fine,
            oracle=broken,
            evidence=lambda: "unused",
        )


def test_unknown_version_abstains():
    with pytest.raises(pytest.skip.Exception, match="deployed version unknown"):
        judge_by_version(
            UNKNOWN,
            "2.4.0",
            "BUG-7",
            SIGNATURE,
            coverage=EvidenceVerdict("seat proration"),
            control=fine,
            oracle=fine,
            evidence=lambda: "unused",
        )


def test_detail_prefix_nests_and_resets():
    with detail_prefix("[a]"), detail_prefix("[b]"):
        record_verdict("coverage", "recorded", subject="s", detail="x")
    record_verdict("coverage", "recorded", subject="s", detail="y")
    assert [r["detail"] for r in read_verdicts()] == ["[a] [b] x", "y"]


# --- document fingerprint ----------------------------------------------------------------


def demo_document() -> str:
    return (ROOT / "demo_sut" / "openapi.json").read_text(encoding="utf-8")


def fetch_from(documents: dict[str, tuple[int, str]]):
    def fetch(path: str):
        return documents.get(path, (404, ""))

    return fetch


NEW_SENTENCE = DocumentFingerprint("/openapi.json", "/info/description", "X-Demo-Clock")
FUTURE_SENTENCE = DocumentFingerprint("/openapi.json", "/info/description", "seat proration")


def test_fingerprint_matched_absent_unreadable():
    fetch = fetch_from({"/openapi.json": (200, demo_document())})
    assert NEW_SENTENCE.read(fetch).state is Match.MATCHED
    assert FUTURE_SENTENCE.read(fetch).state is Match.ABSENT
    missing = DocumentFingerprint("/openapi.json", "/info/x-release-notes", "anything")
    assert missing.read(fetch).state is Match.ABSENT
    assert NEW_SENTENCE.read(fetch_from({})).state is Match.UNREADABLE
    assert NEW_SENTENCE.read(fetch_from({"/openapi.json": (200, "<html>")})).state is (
        Match.UNREADABLE
    )


def test_json_pointer_escapes():
    doc = {"paths": {"/api/jobs": {"get": {"summary": "List jobs"}}}, "a~b": [1, 2]}
    assert json_pointer(doc, "/paths/~1api~1jobs/get/summary") == "List jobs"
    assert json_pointer(doc, "/a~0b/1") == 2
    assert json_pointer(doc, "") is doc
    with pytest.raises(ValueError):
        json_pointer(doc, "paths")


def test_fingerprint_asserted_where_expected_recorded_elsewhere():
    fetch = fetch_from({"/openapi.json": (200, demo_document())})
    coverage = EvidenceVerdict("release 2.4 deployed")
    fingerprint_verdict("staging", {"staging"}, NEW_SENTENCE.read(fetch), coverage)
    fingerprint_verdict("qa", {"staging"}, FUTURE_SENTENCE.read(fetch), coverage)
    rows = [(r["verdict"], r["detail"].split(":")[0]) for r in read_verdicts()]
    assert rows == [
        ("proved", "build fingerprint on 'staging'"),
        ("recorded", "build fingerprint on 'qa'"),
    ]
    with pytest.raises(AssertionError, match="fingerprint is absent"):
        fingerprint_verdict("staging", {"staging"}, FUTURE_SENTENCE.read(fetch), coverage)
    with pytest.raises(pytest.skip.Exception, match="unreadable"):
        fingerprint_verdict("staging", {"staging"}, NEW_SENTENCE.read(fetch_from({})), coverage)


def test_demo_document_is_valid_json_with_the_pointer():
    assert "description" in json.loads(demo_document())["info"]
