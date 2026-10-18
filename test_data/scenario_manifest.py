"""Case manifests: declared cases that become parametrized tests, each with an honest state.

A manifest is the one list of cases a feature has to handle::

    {"suite": "storage-changes",
     "cases": [
       {"id": "ST-QUOTA-1", "title": "double the storage quota", "state": "automated",
        "test": "tests/test_storage.py::test_case", "seen_passing": true,
        "proof": "pytest tests/test_storage.py, two green runs, 2026-10-01",
        "inputs": {"action": "change", "ends_in": "active"}},
       {"id": "ST-TIER-1", "title": "switch to the archive tier", "state": "planned",
        "waiting_on": "the tier cannot be edited after creation"},
       {"id": "ST-HOLD-1", "title": "place a legal hold", "state": "manual",
        "why_manual": "no transition leads into the held state"}]}

:meth:`CaseManifest.params` turns cases into ``pytest.param`` objects with the case id as
the test id; a case that cannot be driven (manual, or planned and waiting on something) is
skipped with its reason, so the gap stays visible in every report. ``proven`` counts only
cases that are automated **and** seen passing with a proof - an automated case nobody has
watched pass is a claim, not evidence.

There is one schema. Keys from any other layout are not read, so a stale manifest fails to
load instead of loading empty.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pytest


class CaseState(str, Enum):
    AUTOMATED = "automated"
    PLANNED = "planned"
    MANUAL = "manual"


class ManifestError(ValueError):
    """A case is malformed; the message names it."""


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    state: CaseState
    inputs: dict[str, Any] = field(default_factory=dict)
    test: str | None = None
    seen_passing: bool = False
    proof: str | None = None
    waiting_on: str | None = None
    why_manual: str | None = None

    @property
    def is_proven(self) -> bool:
        return self.state is CaseState.AUTOMATED and self.seen_passing and bool(self.proof)

    @property
    def is_drivable(self) -> bool:
        return self.state is not CaseState.MANUAL and not self.waiting_on


@dataclass(frozen=True)
class CaseManifest:
    suite: str
    cases: tuple[Case, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CaseManifest:
        rows = raw.get("cases")
        if not isinstance(rows, list) or not rows:
            raise ManifestError("manifest has no cases")
        cases, seen = [], set()
        for i, row in enumerate(rows):
            cid = row.get("id")
            where = f"case {i} ({cid})"
            if not cid or not row.get("title"):
                raise ManifestError(f"{where}: id and title are required")
            if cid in seen:
                raise ManifestError(f"{where}: duplicate id")
            seen.add(cid)
            try:
                state = CaseState(row.get("state"))
            except ValueError:
                raise ManifestError(f"{where}: unknown state {row.get('state')!r}") from None
            if state is CaseState.AUTOMATED and not row.get("test"):
                raise ManifestError(f"{where}: an automated case must name its test")
            if state is CaseState.PLANNED and not row.get("waiting_on"):
                raise ManifestError(f"{where}: a planned case must say what it is waiting_on")
            if state is CaseState.MANUAL and not row.get("why_manual"):
                raise ManifestError(f"{where}: a manual case must give why_manual")
            if row.get("seen_passing") and not row.get("proof"):
                raise ManifestError(f"{where}: seen_passing needs a proof")
            cases.append(
                Case(
                    id=cid,
                    title=row["title"],
                    state=state,
                    inputs=dict(row.get("inputs", {})),
                    test=row.get("test"),
                    seen_passing=bool(row.get("seen_passing")),
                    proof=row.get("proof"),
                    waiting_on=row.get("waiting_on"),
                    why_manual=row.get("why_manual"),
                )
            )
        return cls(str(raw.get("suite", "")), tuple(cases))

    @classmethod
    def load(cls, path: str | Path) -> CaseManifest:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def params(self, *states: CaseState) -> list[Any]:
        """``pytest.param`` per case (optionally only some states); undrivable cases skip."""
        wanted = set(states) or set(CaseState)
        out = []
        for case in self.cases:
            if case.state not in wanted:
                continue
            marks = []
            if not case.is_drivable:
                reason = case.waiting_on or case.why_manual or "not drivable"
                marks.append(pytest.mark.skip(reason=reason))
            out.append(pytest.param(case, id=case.id, marks=marks))
        return out

    def summary(self) -> dict[str, int]:
        counts = Counter(c.state.value for c in self.cases)
        counts["proven"] = sum(1 for c in self.cases if c.is_proven)
        return dict(counts)
