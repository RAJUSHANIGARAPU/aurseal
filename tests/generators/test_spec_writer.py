"""The spec writer: the committed spec is exactly what the writer produces from the demo machine."""

from __future__ import annotations

from pathlib import Path

from generators.spec_writer import render_spec, write_spec
from generators.workflow_model import from_mapping, load

ROOT = Path(__file__).resolve().parents[2]
MACHINE = ROOT / "demo_sut" / "config" / "subscription.machine.yaml"
COMMITTED = ROOT / "specs" / "subscription.md"


def test_committed_spec_matches_a_fresh_rendering():
    """Golden file. On a deliberate change: python -m generators.cli spec <machine>."""
    assert render_spec(load(MACHINE)) == COMMITTED.read_text(encoding="utf-8")


def test_spec_has_the_specs_front_matter_and_every_section():
    text = render_spec(load(MACHINE))
    assert text.startswith("---\nseed: false\nfeature: subscription\n---\n")
    for heading in (
        "## States and actions",
        "## Automatic transitions",
        "## Fields",
        "## Reachability",
        "## Scenario create a subscription",
        "## Scenario confirm_cancel from changing",
        "## Scenario field validation for a subscription",
        "## Scenario happy path draft->pending_payment->active->changing->active",
    ):
        assert heading in text, heading
    assert "### `suspended` - Suspended (UNREACHABLE)" in text
    assert "- unreachable: suspended" in text
    assert "`po_reference` | text | no | pattern `^[A-Za-z0-9" in text
    assert "when activate_immediately == true" in text


def test_every_scenario_has_the_four_parts_the_generator_reads():
    text = render_spec(load(MACHINE))
    scenarios = text.split("## Scenario ")[1:]
    assert len(scenarios) >= 10
    for body in scenarios:
        for part in ("### Preconditions", "### Steps", "### Expected", "### Tags"):
            assert part in body


def test_unreachable_states_get_no_action_scenario():
    assert "## Scenario resume from suspended" not in render_spec(load(MACHINE))


def test_automatic_transitions_and_no_fields_are_rendered(tmp_path):
    d = from_mapping(
        {
            "key": "ticket",
            "initial_states": ["open"],
            "states": {
                "open": {"transitions": [{"to": "closed", "condition": "idle > 30d"}]},
                "closed": {},
            },
        }
    )
    path = write_spec(d, tmp_path / "out")
    text = path.read_text()
    assert path.name == "ticket.md"
    assert "- `open` -> `closed` - when idle > 30d" in text
    assert "| - | - | - | - |" in text
    assert "## Scenario field validation" not in text
