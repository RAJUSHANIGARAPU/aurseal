"""Every metric row the engines write validates against its published JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checks._sink import stamp

jsonschema = pytest.importorskip("jsonschema")
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "metrics"

ROWS = {
    "metric_db_snapshot": {"name": "orders", "phase": "before", "row_count": 2, "digest": "ab12"},
    "metric_a11y_scan": {"route": "/subscriptions", "rules_run": 90, "violations": 1},
    "metric_a11y_violation": {
        "route": "/subscriptions",
        "rule_id": "label",
        "impact": "critical",
        "wcag_tags": "wcag2a,wcag412",
        "nodes": 1,
        "known_defect": None,
    },
    "metric_contract_change": {
        "consumer": "web",
        "provider": "api",
        "from_version": "1.0.0",
        "to_version": "2.0.0",
        "change_type": "breaking",
        "location": "GET /users",
        "detail": "required field 'id' removed from response",
        "source": "capture",
    },
    "metric_fault_injection": {
        "spec_id": "f-1",
        "kind": "status",
        "url_glob": "**/api/subscriptions*",
        "fired": 1,
        "oracle": "error banner shown",
        "oracle_outcome": "pass",
    },
    "metric_load_run": {
        "target": "http://127.0.0.1:1/api/health",
        "tool": "k6",
        "thresholds_json": "[]",
        "breached_json": "[]",
        "p95_ms": 12.5,
        "error_rate": 0.0,
    },
    "metric_mutation_score": {
        "package": "gate",
        "killed": 2330,
        "survived": 915,
        "timeout": 0,
        "no_tests": 0,
        "score": 0.718,
        "threshold": 0.68,
    },
    "metric_mutation_survivor": {"package": "gate", "mutant_id": "gate.shard.x_plan__mutmut_3"},
    "metric_security_finding": {
        "tool": "bandit",
        "rule_id": "B324",
        "severity": "high",
        "location": "gate/shard.py:59",
        "fingerprint": "bandit:B324:gate/shard.py:96053cc5c403",
        "allowlisted": 1,
        "allowlist_expires": "2027-01-16",
    },
    "metric_security_scan": {"tool": "bandit", "findings": 3, "blocking": 0, "exit_code": 1},
    "metric_shard_run": {
        "shard_index": 1,
        "shard_count": 3,
        "planned_s": 1.5,
        "actual_s": 2.0,
        "selected": 4,
        "executed": 4,
        "exit_code": 0,
    },
    "metric_visual_diff": {
        "name": "demo-login",
        "browser": "chromium",
        "outcome": "match",
        "diff_pixels": 0,
        "diff_ratio": 0.0,
        "threshold": 0.001,
    },
    "metric_synthetic_probe": {
        "profile": "customer",
        "mode": "invalid",
        "seed": 7,
        "record_index": 2,
        "strategy": "missing",
        "broken_constraint": "name.required",
        "expected": "reject",
        "observed_status": 422,
        "verdict": "pass",
    },
}


@pytest.mark.parametrize("table", sorted(ROWS))
def test_row_validates(table):
    schema = json.loads((SCHEMAS / f"{table}.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(stamp([ROWS[table]], env={"TEST_RUN_ID": "r1"})[0], schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"run_id": "r1"}, schema)


def test_every_schema_has_a_sample_row():
    assert {p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.json")} <= set(ROWS)
