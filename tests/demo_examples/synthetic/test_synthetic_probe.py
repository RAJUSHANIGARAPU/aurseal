"""Synthetic records against the demo service: valid ones accepted, invalid ones judged.

Runs against a private demo instance (its own database), because the probe creates reseller
terms and other examples share the session instance.

The demo checks presence and ranges on reseller terms but not every type. Whatever the
probe finds is pinned as a finding below, so a change to the demo's validation shows up
here instead of passing silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.clients.http_engine import HttpEngine
from demo_sut.server import DemoServer
from test_data.synthetic import Profile, SyntheticGenerator, probe
from test_data.synthetic.probe import FAIL, PASS
from tests.plugins._tier4 import load

ROOT = Path(__file__).resolve().parents[3]
PROFILE = Profile.from_json(ROOT / "test_data" / "profiles" / "reseller_terms.json")
#: A numeric name is stored as text: an invalid record the demo accepts.
KNOWN_FINDINGS = {("name.type", "invalid record accepted (201)")}


@pytest.fixture(scope="module")
def private_demo(tmp_path_factory):
    server = DemoServer(db_path=tmp_path_factory.mktemp("synthetic") / "demo.sqlite").start()
    try:
        yield server.base_url
    finally:
        server.stop()


def _all_modes(seed: int):
    generator = SyntheticGenerator(seed)
    records = generator.generate(PROFILE, 5, "valid")
    for mode in ("boundary", "invalid"):
        records += generator.generate(PROFILE, len(generator.cases(PROFILE, mode)), mode)
    return records


def test_valid_and_boundary_records_are_accepted_and_invalid_ones_judged(private_demo):
    http = HttpEngine(private_demo, max_retries=0)
    results = probe(PROFILE.endpoint, http, _all_modes(seed=7))

    accepted = [r for r in results if r.expected == "accept"]
    assert accepted and all(r.verdict == PASS for r in accepted), [
        (r.record.strategy, r.status) for r in accepted if r.verdict != PASS
    ]
    strategies = {r.record.strategy for r in accepted}
    assert {"rtl_override", "zero_width", "emoji", "long_256"} <= strategies

    rejected = {
        r.record.broken_constraint for r in results if r.verdict == PASS and r.status == 422
    }
    assert {
        "name.required",
        "share_percent.required",
        "share_percent.max",
        "flat_fee.min",
    } <= rejected

    findings = {(r.record.broken_constraint, r.detail) for r in results if r.verdict == FAIL}
    assert findings == KNOWN_FINDINGS, "the demo's validation changed: update KNOWN_FINDINGS"


def test_plugin_reports_fail_with_the_findings(private_demo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    plugin = load("synthetic_data_edge").SyntheticDataEdgePlugin()
    result = plugin.run({"profile": PROFILE, "target": private_demo, "seed": 7})
    assert result.status == "fail"
    found = {(f["broken_constraint"], f["detail"]) for f in result.findings[0]["failures"]}
    assert found == KNOWN_FINDINGS
    assert (tmp_path / "metrics" / "metric_synthetic_probe.jsonl").is_file()
