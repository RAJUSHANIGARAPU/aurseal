"""Manual test execution: the thin built-in runner's rules, framework- and storage-free.

A manual case is the repository's spec format (``## Scenario <name>`` with ``### Steps``,
``### Expected`` and requirement keys under ``### Tags``), so one spec can be executed by
hand or generated into an automated test. Executing it pins the case revision (a hash of
its steps and expected outcomes); a tester records each step as pass / fail / blocked /
n/a with what actually happened and an optional evidence link; finishing maps the steps
to one result in the run gate's outcome vocabulary:

* any ``fail`` -> ``failed`` (the message names the first failing step);
* otherwise any ``blocked`` -> ``skipped`` with the reason (blocked work was **not
  executed**, so it can never count as a pass);
* every step ``na`` -> ``skipped``;
* otherwise -> ``passed``.

A manual pass is ``recorded`` evidence for its requirements, and becomes ``proved`` only
when every passing step carries an evidence link: a tester's word is weaker than an
artifact, and the verdict says which one the release is resting on.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

STEP_STATUSES = ("pass", "fail", "blocked", "na")
_REQUIREMENT = re.compile(r"\b([A-Z][A-Z0-9]+-[0-9]+)\b")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


@dataclass
class ManualCase:
    key: str
    title: str
    steps: list[str]
    expected: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "steps": self.steps,
            "expected": self.expected,
            "preconditions": self.preconditions,
            "requirements": self.requirements,
        }


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "case"


def parse_spec(markdown: str, *, prefix: str = "manual") -> list[ManualCase]:
    """Every ``## Scenario`` of a spec file; steps are its numbered ``### Steps`` lines."""
    body = markdown
    if body.lstrip().startswith("---"):
        parts = body.lstrip().split("---", 2)
        body = parts[2] if len(parts) == 3 else ""
    cases: list[ManualCase] = []
    current: ManualCase | None = None
    section = ""
    for line in body.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower().startswith("scenario"):
                title = title[len("scenario") :].strip(" :")
            current = ManualCase(key=f"{prefix}::{slug(title)}", title=title, steps=[])
            cases.append(current)
            section = ""
            continue
        if current is None:
            continue
        if line.startswith("### "):
            section = line[4:].strip().lower()
            continue
        if section == "steps":
            match = _NUMBERED.match(line)
            if match:
                current.steps.append(match.group(1))
        elif section in ("expected", "preconditions"):
            match = _BULLET.match(line)
            if match:
                getattr(current, section).append(match.group(1))
        elif section in ("tags", "requirements"):
            for req in _REQUIREMENT.findall(line):
                if req not in current.requirements:
                    current.requirements.append(req)
    return [c for c in cases if c.steps]


def revision_id(case: Mapping[str, Any]) -> str:
    """What a tester executes: the steps and the expected outcomes, nothing else."""
    basis = json.dumps(
        {"steps": list(case["steps"]), "expected": list(case.get("expected") or [])},
        sort_keys=True,
        ensure_ascii=False,
    )
    return "m-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str  # execution status: passed | failed | blocked | skipped
    outcome: str  # result outcome (run gate vocabulary)
    message: str = ""
    skip_reason: str | None = None
    coverage: str | None = None  # "proved" | "recorded" | None


def missing_steps(step_count: int, recorded: Sequence[Mapping[str, Any]]) -> list[int]:
    done = {int(s["step_no"]) for s in recorded}
    return [n for n in range(1, step_count + 1) if n not in done]


def outcome_of(steps: Sequence[Mapping[str, Any]]) -> ExecutionOutcome:
    ordered = sorted(steps, key=lambda s: int(s["step_no"]))
    failed = [s for s in ordered if s["status"] == "fail"]
    if failed:
        first = failed[0]
        detail = f": {first['actual']}" if first.get("actual") else ""
        return ExecutionOutcome("failed", "failed", f"step {first['step_no']} failed{detail}")
    blocked = [s for s in ordered if s["status"] == "blocked"]
    if blocked:
        first = blocked[0]
        detail = f": {first['actual']}" if first.get("actual") else ""
        reason = f"blocked at step {first['step_no']}{detail}"
        return ExecutionOutcome("blocked", "skipped", reason, skip_reason=reason)
    if all(s["status"] == "na" for s in ordered):
        reason = "every step was not applicable"
        return ExecutionOutcome("skipped", "skipped", reason, skip_reason=reason)
    passing = [s for s in ordered if s["status"] == "pass"]
    proved = all(s.get("evidence_uri") for s in passing)
    return ExecutionOutcome("passed", "passed", coverage="proved" if proved else "recorded")


def steps_text(steps: Sequence[str], recorded: Sequence[Mapping[str, Any]]) -> str:
    by_no = {int(s["step_no"]): s for s in recorded}
    lines = []
    for n, text in enumerate(steps, 1):
        rec = by_no.get(n, {})
        line = f"step {n} [{rec.get('status', '-')}] {text}"
        if rec.get("actual"):
            line += f" - actual: {rec['actual']}"
        lines.append(line)
    return "\n".join(lines)


__all__ = [
    "STEP_STATUSES",
    "ExecutionOutcome",
    "ManualCase",
    "missing_steps",
    "outcome_of",
    "parse_spec",
    "revision_id",
    "slug",
    "steps_text",
]
