"""Requirement ids: format, prefix namespace, registry states, verdict-named ids."""

from __future__ import annotations

import pytest

from traceability._util import SchemaError
from traceability.requirements import (
    InvalidRequirementId,
    Namespace,
    Registry,
    ids_in_verdict,
    parse_id,
)
from verification.defect_detector import require_defect_id, still_reproduces
from verification.verdicts import suppress_recording


def test_a_well_formed_requirement_id_parses():
    assert parse_id(" REQ-12 ") == "REQ-12"


@pytest.mark.parametrize("raw", ["req-12", "REQ12", "REQ-", "12", "", "REQ-1a"])
def test_a_malformed_id_is_rejected(raw):
    with pytest.raises(InvalidRequirementId, match="does not match"):
        parse_id(raw)


def test_a_defect_prefix_is_not_a_requirement_id():
    with pytest.raises(InvalidRequirementId, match="not a requirement id"):
        parse_id("BUG-142")


def test_prefixes_and_pattern_come_from_the_environment():
    ns = Namespace.from_env({"REQUIREMENT_ID_PREFIXES": "US, EPIC"})
    assert ns.is_requirement_id("US-4") and not ns.is_requirement_id("REQ-4")
    custom = Namespace.from_env({"REQUIREMENT_ID_PATTERN": r"^REQ\.\d+$"})
    assert custom.problem("REQ.3") == "prefix"  # shape ok, split on '-' finds no REQ prefix
    assert custom.problem("REQ-3") == "format"


def test_registry_states(registry_file):
    reg = Registry.load(registry_file)
    assert reg.status("REQ-1") == "active"
    assert reg.status("REQ-9") == "retired"
    assert reg.status("REQ-77") == "unknown"
    assert [r.id for r in reg.active()] == ["REQ-1", "REQ-2", "REQ-3"]
    assert reg.get("REQ-1").priority == "must"


def test_a_missing_registry_makes_every_id_unknown(tmp_path):
    reg = Registry.load(tmp_path / "nope.yaml")
    assert reg.present is False and reg.status("REQ-1") == "unknown"


def test_the_default_registry_is_found_under_the_rootdir(registry_file):
    reg = Registry.load(rootdir=registry_file.parents[1])
    assert reg.path == registry_file and len(reg) == 4


def test_an_invalid_registry_is_rejected_by_its_schema(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text("requirements:\n  - id: REQ-1\n    title: x\n    priority: urgent\n")
    with pytest.raises(SchemaError, match="priority"):
        Registry.load(path)


def test_a_duplicate_id_is_rejected(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text("requirements:\n  - {id: REQ-1, title: a}\n  - {id: REQ-1, title: b}\n")
    with pytest.raises(ValueError, match="listed twice"):
        Registry.load(path)


def test_the_repository_registry_loads():
    reg = Registry.load(rootdir=__import__("pathlib").Path(__file__).resolve().parents[2])
    assert reg.present and len(reg.active()) >= 1


def test_coverage_verdict_names_a_requirement_by_ticket_or_subject():
    assert ids_in_verdict({"ticket": "REQ-4", "subject": "invoice totals"}) == ["REQ-4"]
    assert ids_in_verdict({"ticket": None, "subject": "REQ-12 renewal date"}) == ["REQ-12"]
    # a defect ticket on a coverage verdict is not a requirement link
    assert ids_in_verdict({"ticket": "BUG-142", "subject": "REQ-12: totals"}) == ["REQ-12"]
    assert ids_in_verdict({"ticket": "BUG-142", "subject": "totals"}) == []


def test_a_requirement_id_is_refused_as_a_defect_id():
    assert require_defect_id(" BUG-142 ") == "BUG-142"
    with pytest.raises(ValueError, match="requirement id, not a defect id"):
        require_defect_id("REQ-12")
    with suppress_recording(), pytest.raises(ValueError, match="requirement id"):
        still_reproduces(
            "REQ-12", "totals differ by one cent", oracle=lambda: None, exercised=lambda: None
        )
