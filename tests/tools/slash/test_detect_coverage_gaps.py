"""detect-coverage-gaps: scenario and operation matching, the format guide, the report."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import coverage_gaps as cg

SPEC = """---
seed: true
feature: checkout
---

## Scenario: Pay with a saved card
### Steps
1. pay
### Tags
`smoke` `regression`

## Scenario Apply a voucher
### Tags
`regression`

## Scenario: Cancel before paying
"""

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/users": {"get": {"tags": ["users"]}, "post": {"tags": ["users"]}},
        "/users/{id}": {"get": {"tags": ["users"]}, "delete": {"tags": ["users"]}},
    },
}


def make_tests(tmp_path: Path) -> cg.TestInventory:
    ui, api = tmp_path / "ui", tmp_path / "api"
    ui.mkdir()
    api.mkdir()
    (ui / "test_checkout.py").write_text(
        "def test_pay_with_a_saved_card_visa():\n    pass\n\n"
        "class TestC:\n    def test_Apply_A_Voucher(self):\n        pass\n",
        encoding="utf-8",
    )
    (api / "test_users.py").write_text(
        "def test_user(client, uid):\n    client.get(f'/users/{uid}')\n    client.post('/other')\n",
        encoding="utf-8",
    )
    return cg.inventory((ui,), (api,))


def test_two_of_three_scenarios_covered(tmp_path):
    spec = tmp_path / "checkout.md"
    spec.write_text(SPEC, encoding="utf-8")
    parsed = cg.parse_spec(spec)
    assert parsed.feature == "checkout"
    assert [s.name for s in parsed.scenarios] == [
        "Pay with a saved card",
        "Apply a voucher",
        "Cancel before paying",
    ]
    assert parsed.scenarios[0].tags == ["smoke", "regression"]
    result = cg.check_spec(parsed, make_tests(tmp_path))
    assert (result.covered, len(result.items)) == (2, 3)
    assert result.missing == ["Cancel before paying"]


def test_a_spec_with_no_matching_tests_is_listed_under_no_tests(tmp_path):
    spec = tmp_path / "other.md"
    spec.write_text("---\nfeature: other\n---\n## Scenario: Something new\n", encoding="utf-8")
    inv = make_tests(tmp_path)
    result = cg.check_spec(cg.parse_spec(spec), inv)
    report = cg.render([result], inv, date(2026, 10, 17))
    none_section = report.split("## No tests at all")[1].split("## Partially")[0]
    assert "other.md" in none_section


def test_the_format_guide_is_skipped(tmp_path):
    (tmp_path / "README.md").write_text("## Scenario: Example\n", encoding="utf-8")
    (tmp_path / "real.md").write_text("## Scenario: Real one\n", encoding="utf-8")
    assert [p.name for p in cg.spec_files(tmp_path)] == ["real.md"]


def test_openapi_one_of_four_operations_referenced(tmp_path):
    ops = cg.openapi_operations(OPENAPI)
    result = cg.check_api("doc.json", "openapi", ops, make_tests(tmp_path))
    assert (result.covered, len(result.items)) == (1, 4)
    assert sorted(result.missing) == ["DELETE /users/{id}", "GET /users", "POST /users"]


def test_a_covers_marker_counts_as_coverage(tmp_path):
    inv = cg.TestInventory(names={"test_x"}, covers={"DELETE /users/{id}"})
    assert cg.operation_covered(cg.Operation("DELETE", "/users/{id}"), inv)
    assert not cg.operation_covered(cg.Operation("GET", "/users/{id}"), inv)


def test_postman_requests_are_endpoints():
    doc = {
        "info": {"name": "c", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/"},
        "item": [
            {
                "name": "Users",
                "item": [{"name": "List", "request": {"method": "GET", "url": "{{base}}/users"}}],
            },
            {"name": "One", "request": {"method": "GET", "url": {"path": ["users", ":id"]}}},
        ],
    }
    assert cg.kind_of(doc) == "postman"
    assert [op.key for op in cg.postman_operations(doc)] == ["GET /users", "GET /users/{id}"]


def test_report_is_saved_with_the_date(tmp_path):
    path = cg.save("# x\n", tmp_path, date(2026, 10, 17))
    assert path.name == "coverage-gaps-2026-10-17.md" and path.exists()


def test_the_repository_report_runs(tmp_path):
    results = cg.collect("all")
    assert any(r.kind == "openapi" for r in results)
    assert all(not cg.is_format_guide(Path(r.source)) for r in results)
    json.dumps([r.__dict__ for r in results])


def test_the_command_asks_before_generating_and_may_be_model_invoked():
    meta, body = read_command("detect-coverage-gaps")
    assert "disable-model-invocation" not in meta
    assert claude_md_lists("detect-coverage-gaps")
    assert "explicit yes" in body and "Without a yes, nothing is generated" in body
    assert "coverage-gaps-<date>.md" in body
