"""The neutral workflow YAML model: parsing, unknown keys, consistency findings, directories."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from generators.workflow_model import WorkflowError, from_mapping, load, load_dir

ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"

SMALL = {
    "key": "order",
    "title": "Order",
    "initial_states": ["new"],
    "vendor_extension": {"ignored": True},
    "states": {
        "new": {
            "title": "New",
            "on_entry": "stamp",
            "transitions": [
                {"action": "place", "to": "placed", "condition": "lines > 0", "colour": "x"},
                {"to": "expired"},
            ],
        },
        "placed": {"transitions": []},
        "expired": {},
    },
    "actions": {"place": {"title": "Place", "operations": ["reserve"], "icon": "cart"}},
    "fields": [{"name": "lines", "type": "integer", "required": True, "min": 1, "widget": "x"}],
}


def test_the_demo_machine_parses_into_frozen_dataclasses():
    d = load(MACHINE)
    assert d.key == "subscription" and d.initial_states == ("draft",)
    assert list(d.states)[:3] == ["draft", "pending_payment", "active"]
    assert d.states["draft"].actions == ("submit", "activate")
    activate = d.states["draft"].transitions[1]
    assert activate.to == "active" and activate.condition == "activate_immediately == true"
    assert d.actions["renew"].condition.startswith("term_end within")
    assert d.actions["confirm_cancel"].operations[-1] == "issue_closure_notice"
    po = next(f for f in d.fields if f.name == "po_reference")
    assert po.pattern and not po.required
    assert next(f for f in d.fields if f.name == "frequency").choices == (
        "MONTHLY",
        "QUARTERLY",
        "YEARLY",
    )
    assert d.problems() == []
    with pytest.raises(AttributeError):
        d.key = "other"  # frozen


def test_unknown_keys_are_ignored_and_a_transition_without_action_is_automatic():
    d = from_mapping(SMALL)
    assert d.states["new"].on_entry == ("stamp",)
    auto = d.states["new"].transitions[1]
    assert auto.automatic and auto.action is None and auto.label == "(automatic)"
    assert d.automatic_transitions() == [("new", auto)]
    assert d.states["placed"].is_terminal and d.states["expired"].is_terminal
    assert d.fields[0].minimum == 1 and d.fields[0].required


def test_problems_name_every_inconsistency_without_raising():
    raw = {
        "key": "x",
        "initial_states": ["start", "ghost"],
        "states": {"start": {"transitions": [{"action": "go", "to": "nowhere"}]}},
    }
    problems = from_mapping(raw).problems()
    assert "initial state 'ghost' is not a declared state" in problems
    assert any("nowhere: target state is not declared" in p for p in problems)
    assert any("go-->: action is not declared" in p for p in problems)


@pytest.mark.parametrize("raw", [[], {"key": "x"}, {"states": {}}, "text"])
def test_a_non_workflow_is_rejected(raw):
    with pytest.raises(WorkflowError):
        from_mapping(raw)


def test_load_reports_missing_and_broken_files(tmp_path):
    with pytest.raises(WorkflowError, match="cannot read"):
        load(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: [unclosed")
    with pytest.raises(WorkflowError, match="not valid YAML"):
        load(bad)


def test_load_dir_keeps_workflows_and_skips_other_yaml(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "order.yml").write_text(yaml.safe_dump(SMALL))
    (tmp_path / "settings.yaml").write_text("timeout: 3\n")
    (tmp_path / "notes.txt").write_text("key: x")
    found = load_dir(tmp_path)
    assert [d.key for d in found] == ["order"]
