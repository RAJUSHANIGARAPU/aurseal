"""Config-version preflight: rejected, orphaned, stuck and not-assessed records, offline."""

from __future__ import annotations

import ast
import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from gate.preflight import Status
from gate.read_only import find_write_calls
from tools.config_preflight import cli
from tools.config_preflight import config_preflight as cp

ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = ROOT / "tools" / "config_preflight"
DEMO_MACHINE = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"

OLD: dict[str, Any] = {
    "key": "subscription",
    "initial_states": ["draft"],
    "states": {
        "draft": {"transitions": [{"action": "submit", "to": "active"}]},
        "active": {
            "transitions": [
                {"action": "cancel", "to": "cancelled"},
                {"action": "pause", "to": "paused"},
            ],
            "fields": [{"name": "start_date", "required": True}],
        },
        "paused": {"transitions": [{"action": "resume", "to": "active"}]},
        "cancelled": {"transitions": []},
    },
    "actions": {"submit": {}, "cancel": {}, "pause": {}, "resume": {}},
    "fields": [
        {"name": "plan_id", "type": "reference", "required": True, "ref": "plans"},
        {"name": "frequency", "type": "choice", "choices": ["MONTHLY", "QUARTERLY", "YEARLY"]},
        {"name": "seats", "type": "integer", "min": 1, "max": 500},
        {"name": "start_date", "type": "date"},
        {"name": "note", "type": "text", "max_len": 200},
    ],
}


def new_config(change) -> dict[str, Any]:
    raw = copy.deepcopy(OLD)
    change(raw)
    return raw


def field_entry(raw: dict[str, Any], name: str) -> dict[str, Any]:
    return next(f for f in raw["fields"] if f["name"] == name)


def record(rid: str, state: str = "active", **values: Any) -> dict[str, Any]:
    base = {"plan_id": "PLN-1", "frequency": "MONTHLY", "seats": 10, "start_date": "2026-01-01"}
    return {"id": rid, "type": "subscription", "state": state, "fields": {**base, **values}}


def preflight(new_raw: dict[str, Any], records: list[dict[str, Any]], old_raw=None):
    old = {"subscription": cp.entity_from_mapping(old_raw or OLD, "old")}
    new = {"subscription": cp.entity_from_mapping(new_raw, "new")}
    return cp.run(old, new, records)


# ---------------------------------------------------------------- clean upgrade


def test_clean_upgrade_counts_records_and_lists_nothing():
    records = [record("S-1"), record("S-2", state="draft"), record("S-3", state="cancelled")]
    result = preflight(copy.deepcopy(OLD), records)
    assert (result.total, result.valid, result.findings, result.changes) == (3, 3, [], [])
    report = cp.render_markdown(result)
    assert "3 read, 3 valid under the new config, 0 rejected" in report
    assert "No change." in report
    assert "S-1" not in report  # valid records are counted, not listed


# ---------------------------------------------------------------- rejected


def test_narrowed_choices_reject_with_rule_field_and_value():
    def drop_yearly(raw):
        field_entry(raw, "frequency")["choices"] = ["MONTHLY", "QUARTERLY"]

    result = preflight(new_config(drop_yearly), [record("S-1"), record("S-2", frequency="YEARLY")])
    [finding] = result.of(cp.Finding.REJECTED)
    assert (finding.record, finding.field, finding.rule, finding.value) == (
        "S-2",
        "frequency",
        "choices",
        "YEARLY",
    )
    assert finding.already_under_old is False
    assert result.valid == 1
    [change] = result.changes
    assert (change.change, change.subject) == ("tightened", "frequency.choices")


@pytest.mark.parametrize(
    ("change", "values", "field", "rule"),
    [
        (lambda r: field_entry(r, "seats").update(max=50), {"seats": 120}, "seats", "max"),
        (lambda r: field_entry(r, "seats").update(min=5), {"seats": 2}, "seats", "min"),
        (
            lambda r: field_entry(r, "note").update(max_len=5),
            {"note": "too long"},
            "note",
            "max_len",
        ),
        (
            lambda r: field_entry(r, "plan_id").update(pattern=r"PLN-\d{3}"),
            {"plan_id": "PLN-1"},
            "plan_id",
            "pattern",
        ),
        (
            lambda r: field_entry(r, "note").update(required=True),
            {"note": None},
            "note",
            "required",
        ),
        (lambda r: field_entry(r, "seats").update(type="date"), {}, "seats", "type"),
    ],
)
def test_each_tightened_rule_rejects(change, values, field, rule):
    result = preflight(new_config(change), [record("S-9", **values)])
    rejected = result.of(cp.Finding.REJECTED)
    assert [(f.field, f.rule) for f in rejected] == [(field, rule)]


def test_state_level_rule_applies_only_in_that_state():
    def require_note_when_paused(raw):
        raw["states"]["paused"]["fields"] = [{"name": "note", "required": True}]

    records = [record("S-1"), record("S-2", state="paused")]
    result = preflight(new_config(require_note_when_paused), records)
    assert [f.record for f in result.of(cp.Finding.REJECTED)] == ["S-2"]
    [change] = result.changes
    assert (change.scope, change.change, change.subject) == (
        "fields in paused",
        "tightened",
        "field note",
    )


def test_a_record_the_old_config_already_rejected_is_marked():
    def tighten_max(raw):
        field_entry(raw, "seats")["max"] = 400

    result = preflight(new_config(tighten_max), [record("S-1", seats=900)])
    [finding] = result.of(cp.Finding.REJECTED)
    assert finding.already_under_old is True
    assert "also rejected" in cp.render_markdown(result)


def test_loosened_rule_turns_a_rejected_record_valid():
    def loosen(raw):
        field_entry(raw, "seats")["max"] = 1000

    result = preflight(new_config(loosen), [record("S-1", seats=900)])
    assert (result.valid, result.newly_valid, result.findings) == (1, 1, [])
    assert result.changes[0].change == "loosened"


# ---------------------------------------------------------------- orphaned and stuck


def test_removed_state_orphans_its_records():
    def drop_paused(raw):
        del raw["states"]["paused"]
        raw["states"]["active"]["transitions"].pop()

    result = preflight(new_config(drop_paused), [record("S-1"), record("S-2", state="paused")])
    [finding] = result.of(cp.Finding.ORPHANED)
    assert (finding.record, finding.state) == ("S-2", "paused")
    subjects = {(c.change, c.subject) for c in result.changes}
    assert ("removed", "state paused") in subjects
    assert ("removed", "transition pause -> paused") in subjects


def test_removed_entity_type_orphans_its_records():
    old = {"subscription": cp.entity_from_mapping(OLD)}
    other = {**copy.deepcopy(OLD), "key": "order"}
    result = cp.run(old, {"order": cp.entity_from_mapping(other)}, [record("S-1")])
    [finding] = result.of(cp.Finding.ORPHANED)
    assert finding.detail == "entity type is not in the new config"


def test_state_that_loses_every_exit_is_stuck():
    def no_resume(raw):
        raw["states"]["paused"]["transitions"] = []

    result = preflight(new_config(no_resume), [record("S-1"), record("S-2", state="paused")])
    [finding] = result.of(cp.Finding.STUCK)
    assert finding.record == "S-2"
    assert "resume->active" in finding.detail
    assert result.blocking


def test_a_state_that_was_already_terminal_is_not_stuck():
    result = preflight(copy.deepcopy(OLD), [record("S-1", state="cancelled")])
    assert result.of(cp.Finding.STUCK) == []


# ---------------------------------------------------------------- not assessed


@pytest.mark.parametrize(
    ("change", "rule", "why"),
    [
        (lambda r: field_entry(r, "plan_id").update(unique=True), "unique", "not understood"),
        (lambda r: field_entry(r, "note").update(pattern="(["), "pattern", "does not compile"),
        (lambda r: field_entry(r, "seats").update(type="money"), "type", "not understood"),
        (lambda r: field_entry(r, "plan_id").update(ref="price_plans"), "ref", "target changed"),
    ],
)
def test_a_rule_that_cannot_be_evaluated_is_not_assessed_never_valid(change, rule, why):
    result = preflight(new_config(change), [record("S-1", note="x")])
    [finding] = result.of(cp.Finding.NOT_ASSESSED)
    assert finding.rule == rule
    assert why in finding.detail
    assert result.valid == 0  # unknown is not a pass
    assert "## Not assessed (1)" in cp.render_markdown(result)


def test_record_without_a_state_is_not_assessed():
    result = preflight(copy.deepcopy(OLD), [{"id": "S-1", "type": "subscription"}])
    [finding] = result.of(cp.Finding.NOT_ASSESSED)
    assert finding.detail == "record has no state"
    assert result.valid == 0


def test_check_rule_uses_the_preflight_status_vocabulary():
    assert cp.check_rule("required", True, cp.MISSING)[0] is Status.BLOCKED
    assert cp.check_rule("min", 1, 3) is Status.OK
    assert cp.check_rule("min", "2026-01-01", "2025-12-31")[0] is Status.BLOCKED
    assert cp.check_rule("max", 10, "lots")[0] is Status.UNKNOWN
    assert cp.check_rule("type", "integer", True)[0] is Status.BLOCKED
    assert cp.check_rule("type", "decimal", "12.50") is Status.OK


# ---------------------------------------------------------------- inputs


def test_demo_machine_loads_and_a_narrowed_region_list_rejects(tmp_path):
    raw = yaml.safe_load(DEMO_MACHINE.read_text(encoding="utf-8"))
    for entry in raw["fields"]:
        if entry["name"] == "data_region":
            entry["choices"] = ["EU-CENTRAL", "EU-NORTH"]
    new_file = tmp_path / "subscription.machine.yaml"
    new_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    values = {
        "customer_id": "C-1",
        "partner_id": "P-1",
        "plan_id": "PL-1",
        "frequency": "MONTHLY",
        "start_date": "2026-01-01",
        "invoice_delivery": "EMAIL",
    }
    records = [
        {
            "id": "S-1",
            "type": "subscription",
            "state": "active",
            **values,
            "data_region": "EU-NORTH",
        },
        {
            "id": "S-2",
            "type": "subscription",
            "state": "active",
            **values,
            "data_region": "AP-SOUTH",
        },
    ]
    result = cp.run(cp.load_config(DEMO_MACHINE), cp.load_config(new_file), records)
    assert [(f.record, f.field) for f in result.of(cp.Finding.REJECTED)] == [("S-2", "data_region")]


def test_file_reader_reads_jsonl_and_json(tmp_path):
    jsonl = tmp_path / "records.jsonl"
    jsonl.write_text(json.dumps(record("S-1")) + "\n\n" + json.dumps(record("S-2")) + "\n")
    wrapped = tmp_path / "records.json"
    wrapped.write_text(json.dumps({"records": [record("S-3")]}))
    assert [r["id"] for r in cp.FileRecordReader(jsonl).records()] == ["S-1", "S-2"]
    assert [r["id"] for r in cp.FileRecordReader(wrapped).records()] == ["S-3"]


def test_a_bad_jsonl_line_is_an_input_error_not_a_skipped_record(tmp_path):
    jsonl = tmp_path / "records.jsonl"
    jsonl.write_text(json.dumps(record("S-1")) + "\n{not json\n")
    with pytest.raises(cp.InputError, match="records.jsonl:2"):
        list(cp.FileRecordReader(jsonl).records())


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.test",
         "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
        check=True, capture_output=True,
    )  # fmt: skip


@pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")
def test_two_git_refs_of_one_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    target = repo / "config" / "subscription.yaml"
    target.write_text(yaml.safe_dump(OLD))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "v1")
    _git(repo, "tag", "v1")
    target.write_text(yaml.safe_dump(new_config(lambda r: field_entry(r, "seats").update(max=5))))
    _git(repo, "commit", "-qam", "v2")

    old = cp.load_config_at_ref(repo, "v1", "config/subscription.yaml")
    new = cp.load_config_at_ref(repo, "HEAD", "config")  # a directory works too
    result = cp.run(old, new, [record("S-1")])
    assert [(f.field, f.rule) for f in result.of(cp.Finding.REJECTED)] == [("seats", "max")]
    with pytest.raises(cp.InputError, match="git"):
        cp.load_config_at_ref(repo, "no-such-ref", "config/subscription.yaml")


# ---------------------------------------------------------------- CLI


@pytest.fixture
def files(tmp_path):
    old = tmp_path / "v1.yaml"
    new = tmp_path / "v2.yaml"
    records = tmp_path / "records.jsonl"
    old.write_text(yaml.safe_dump(OLD))
    records.write_text("\n".join(json.dumps(r) for r in [record("S-1"), record("S-2", seats=80)]))
    return old, new, records


def _cli(old, new, records, *extra):
    return cli.main(["--old", str(old), "--new", str(new), "--records", str(records), *extra])


def test_cli_markdown_and_fail_on_reject(files, capsys):
    old, new, records = files
    new.write_text(yaml.safe_dump(new_config(lambda r: field_entry(r, "seats").update(max=50))))
    assert _cli(old, new, records) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Config preflight: v1.yaml -> v2.yaml")
    assert "| S-2 | subscription | active | seats | max | 80 |" in out
    assert _cli(old, new, records, "--fail-on-reject") == 2


def test_cli_json_and_clean_exit(files, capsys):
    old, new, records = files
    new.write_text(yaml.safe_dump(OLD))
    assert _cli(old, new, records, "--json", "--fail-on-reject") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["counts"] == {
        "records": 2,
        "valid": 2,
        "newly_valid": 0,
        "orphaned": 0,
        "out_of_scope": 0,
        "rejected": 0,
        "stuck": 0,
        "stale_action": 0,
        "stale_route": 0,
        "not_assessed": 0,
    }
    assert body["determined"] is True
    assert body["scanned"] == {"subscription": {"file": 2}}


def test_cli_not_assessed_fails_unless_allowed(files):
    old, new, records = files
    new.write_text(yaml.safe_dump(new_config(lambda r: field_entry(r, "seats").update(odd=True))))
    assert _cli(old, new, records, "--fail-on-reject") == 2
    assert _cli(old, new, records, "--fail-on-reject", "--allow-not-assessed") == 0


def test_cli_bad_input_exits_1(files, capsys):
    old, _new, records = files
    assert _cli(old, old.with_name("missing.yaml"), records) == 1
    assert "config not found" in capsys.readouterr().err
    assert cli.main(["--old", str(old), "--new-ref", "HEAD", "--records", str(records)]) == 1


# ---------------------------------------------------------------- read-only


def test_module_cannot_write():
    assert find_write_calls([MODULE_DIR]) == []
    writes = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir", "rename", "touch"}
    for source in MODULE_DIR.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in writes:
                raise AssertionError(f"{source.name}:{node.lineno}: {ast.unparse(func)}")
            if isinstance(func, ast.Name) and func.id == "open":
                raise AssertionError(f"{source.name}:{node.lineno}: open()")
