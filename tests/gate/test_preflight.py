"""Read-only preflight: safety sets a candidate aside, suitability leaves it for others."""

from __future__ import annotations

from types import SimpleNamespace

from gate.preflight import (
    CheckSpec,
    Disposition,
    Kind,
    Status,
    evaluate,
    run_preflight,
)
from gate.read_only import ReadOnlyClient

SPECS = [
    CheckSpec("no open draft", Kind.SAFETY, lambda s: not s["record"].get("draft_open")),
    CheckSpec(
        "owner resolves",
        Kind.SAFETY,
        lambda s: (s["owner"]["status"] != "cancelled", f"owner {s['owner']['status']}"),
    ),
    CheckSpec(
        "fully paid",
        Kind.SUITABILITY,
        lambda s: s["record"].get("balance") == 0,
        need="fully_paid",
    ),
    CheckSpec("has payment method", Kind.SUITABILITY, lambda s: s["record"].get("method")),
]

CLEAN = {
    "record": {"draft_open": False, "balance": 0, "method": "card"},
    "owner": {"status": "active"},
}


def test_clean_candidate_is_used():
    report = evaluate("R-1", CLEAN, SPECS, needs=["fully_paid"])
    assert report.disposition is Disposition.USE
    assert report.reasons() == []


def test_unsafe_candidate_is_set_aside():
    snapshot = {**CLEAN, "owner": {"status": "cancelled"}}
    report = evaluate("R-1", snapshot, SPECS)
    assert report.disposition is Disposition.SET_ASIDE
    assert report.reasons() == ["safety:owner resolves blocked - owner cancelled"]


def test_unsuitable_candidate_is_left_for_other_tests():
    snapshot = {**CLEAN, "record": {**CLEAN["record"], "balance": 10}}
    assert evaluate("R-1", snapshot, SPECS, ["fully_paid"]).disposition is Disposition.NOT_SUITABLE
    # Without the need the suitability check is not asked.
    assert evaluate("R-1", snapshot, SPECS).disposition is Disposition.USE


def test_unknown_safety_is_not_permission():
    report = evaluate("R-1", {"record": CLEAN["record"]}, SPECS)  # owner never read
    check = next(c for c in report.checks if c.name == "owner resolves")
    assert check.status is Status.UNKNOWN and "check raised" in check.detail
    assert report.disposition is Disposition.SET_ASIDE


def test_none_is_unknown_and_tuple_detail_is_kept():
    spec = CheckSpec("observed", Kind.SUITABILITY, lambda s: None)
    assert spec.run({}).status is Status.UNKNOWN
    assert spec.run({}).detail == "not observed"


def test_run_preflight_reads_through_the_read_only_client():
    responses = {
        "https://api.example.test/records/R-1": CLEAN["record"],
        "https://api.example.test/owners/O-1": CLEAN["owner"],
    }
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        if url not in responses:
            raise ConnectionError(url)
        return SimpleNamespace(json=lambda: responses[url])

    client = ReadOnlyClient(get, "https://api.example.test")
    report = run_preflight(
        client, "R-1", {"record": "/records/R-1", "owner": "/owners/O-1"}, SPECS, ["fully_paid"]
    )
    assert report.disposition is Disposition.USE
    assert len(seen) == 2

    failing = run_preflight(client, "R-2", {"record": "/records/R-2"}, SPECS)
    assert failing.disposition is Disposition.SET_ASIDE
