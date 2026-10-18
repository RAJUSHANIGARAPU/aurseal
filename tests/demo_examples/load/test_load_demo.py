"""Real k6 runs against the demo service: a held threshold, a crossed one, a fault.

Skipped where k6 is not installed (the offline job); the nightly checks job installs a
pinned k6 and sets ``CHECKS_REQUIRE_TOOLS=true`` so a missing binary fails instead.
The three cases are the controls the engine needs: one run that must pass, one that must
fail on latency, one that must fail on errors because the demo's API answers 503.
"""

from __future__ import annotations

import json

import pytest

from checks._sink import MemorySink
from checks.load.profile import LoadProfile, Threshold
from checks.load.runner import K6Runner, run_load
from tests.checks._require import require_binary


def _profile(*pairs: tuple[str, str]) -> LoadProfile:
    return LoadProfile(stages=(("2s", 3),), thresholds=tuple(Threshold(m, e) for m, e in pairs))


@pytest.fixture
def k6_bin() -> str:
    return require_binary("k6", "K6_BIN")


def test_generous_thresholds_hold_against_the_demo(demo_sut, demo_control, k6_bin, tmp_path):
    demo_control.faults()
    sink = MemorySink()
    profile = _profile(("http_req_duration", "p(95)<2000"), ("http_req_failed", "rate<0.01"))
    out = run_load(
        [demo_sut.base_url + "/api/health"],
        profile,
        tmp_path,
        runner=K6Runner(k6_bin, 120),
        sink=sink,
    )
    assert out.status == "pass", out.reason
    row = sink.rows["metric_load_run"][0]
    assert row["requests"] > 0 and row["p95_ms"] is not None
    assert json.loads(row["breached_json"]) == []


def test_an_impossible_latency_threshold_fails_and_is_named(
    demo_sut, demo_control, k6_bin, tmp_path
):
    demo_control.faults()
    out = run_load(
        [demo_sut.base_url + "/api/health"],
        _profile(("http_req_duration", "p(95)<0.001")),
        tmp_path,
        runner=K6Runner(k6_bin, 120),
    )
    assert out.status == "fail"
    assert "http_req_duration: p(95)<0.001" in out.reason


def test_an_unavailable_api_breaches_the_error_rate(demo_sut, demo_control, k6_bin, tmp_path):
    demo_control.fault("api_unavailable")
    out = run_load(
        [demo_sut.base_url + "/api/plans"],
        _profile(("http_req_failed", "rate<0.01")),
        tmp_path,
        runner=K6Runner(k6_bin, 120),
    )
    assert demo_control.evidence() == "api_unavailable"
    assert out.status == "fail"
    assert "http_req_failed: rate<0.01" in out.reason
    assert out.metrics["error_rate"] == 1
