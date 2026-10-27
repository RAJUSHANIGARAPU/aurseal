"""A "required" rule probed with explicit empty strings, not only with an absent field.

An empty string satisfies a presence check. The demo switch ``task_title_presence_only``
makes the task title check exactly that, so the absent-field probe still answers 422 while
``""`` and a blank title are stored. The pair:

* control - a task created with a distinct valid title, a value no fallback could produce,
  must surface in the listing with that exact title: the input decides the output;
* probe - every ``required-empty`` variant the synthetic generator emits for ``title`` must
  be refused.

Tasks have no downstream step, so nothing is handed on; the probe only creates.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from test_data.synthetic import Profile, SyntheticGenerator
from test_data.synthetic.generator import REQUIRED_EMPTY
from test_data.synthetic.profile import FieldSpec
from tests.demo_examples import detector_pair

TICKET = "DEMO-119"
TASK = Profile("task", (FieldSpec(name="title", kind="string", required=True, max_len=120),))


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pair switches its own fault on: keep its verdicts out of the run's evidence file."""
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts" / "verdicts.jsonl"))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)


def _empty_variants() -> list[dict]:
    generator = SyntheticGenerator(seed=11)
    cases = len(generator.cases(TASK, "invalid"))
    records = generator.generate(TASK, cases, "invalid")
    variants = [r.values for r in records if r.strategy.startswith(REQUIRED_EMPTY)]
    assert len(variants) == 2, f"expected an empty and a blank variant, got {variants}"
    return variants


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_required_title_refuses_empty_strings_detector(demo_http, demo_control, fault_on):
    demo_control.faults(*(["task_title_presence_only"] if fault_on else []))
    answers = {repr(v["title"]): demo_http.post("tasks", json=v).status for v in _empty_variants()}
    absent = demo_http.post("tasks", json={}).status

    def exercised() -> None:
        distinct = f"Probe title {uuid.uuid4().hex[:8]}"
        created = demo_http.post("tasks", json={"title": distinct})
        assert created.status == 201, f"control: a valid title was refused: {created}"
        listed = {t["id"]: t["title"] for t in demo_http.get("tasks").json()}
        assert listed.get(created.json()["id"]) == distinct, "control: the title did not surface"
        assert absent == 422, "control: an absent title is refused either way"

    def oracle() -> None:
        stored = {value: status for value, status in answers.items() if status != 422}
        assert not stored, f"{TICKET}: an empty required title was not refused: {stored}"

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: an empty required title was not refused",
        oracle=oracle,
        exercised=exercised,
        fault="task_title_presence_only",
        fault_on=fault_on,
    )
