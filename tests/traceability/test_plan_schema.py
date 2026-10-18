"""Plans load only when they validate; the id is the file name; the revision is the blob."""

from __future__ import annotations

from pathlib import Path

import pytest

from results.revision import blob_sha
from traceability.plans import PlanError, load_plan, load_plans

ROOT = Path(__file__).resolve().parents[2]
GOOD = """\
id: p1
scope:
  include:
    - marker: smoke
exit_criteria:
  run_integrity: PASS
"""


def _write(tmp_path, text, name="p1"):
    path = tmp_path / f"{name}.yaml"
    path.write_text(text)
    return path


def test_a_valid_plan_loads_with_its_blob_revision(tmp_path):
    path = _write(tmp_path, GOOD)
    plan = load_plan(path)
    assert plan.id == "p1" and plan.revision == blob_sha(path.read_bytes())
    assert plan.criteria == {"run_integrity": "PASS"}


@pytest.mark.parametrize(
    "criteria",
    [
        "min_pass_rate: 1.5",
        "run_integrity: FAIL",
        "requirements_proved: some",
        "max_open_defects: {any: -1}",
        "unknown_rule: true",
    ],
)
def test_invalid_criteria_are_rejected(tmp_path, criteria):
    text = GOOD.replace("run_integrity: PASS", criteria)
    with pytest.raises(PlanError, match="plan.schema.json"):
        load_plan(_write(tmp_path, text))


def test_no_criteria_is_rejected(tmp_path):
    with pytest.raises(PlanError):
        load_plan(_write(tmp_path, GOOD.replace("  run_integrity: PASS\n", "  {}\n")))


def test_a_selector_names_exactly_one_kind(tmp_path):
    text = GOOD.replace("- marker: smoke", "- {marker: smoke, nodeids: [a]}")
    with pytest.raises(PlanError):
        load_plan(_write(tmp_path, text))


def test_the_id_must_equal_the_file_name(tmp_path):
    with pytest.raises(PlanError, match="must equal the file name"):
        load_plan(_write(tmp_path, GOOD, name="other"))


def test_the_repository_plans_load():
    assert [p.id for p in load_plans(ROOT / "plans")] == ["demo-regression"]
