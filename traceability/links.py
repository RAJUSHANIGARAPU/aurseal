"""Case links: which test claims which requirement, as written once per run.

File: ``reports/traceability/case-links.json`` (``case-links-<lane>.json`` for a named
lane, the same suffix rule as the run-trust marker). Schema:
``traceability/schemas/case-links.schema.json``.

A link is a claim by the test author, not proof: only a ``proved`` coverage verdict
naming the requirement upgrades it (see ``traceability.matrix``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traceability._util import read_json, trace_dir, utc_now, validate, write_json

FILE_STEM = "case-links"
DEFAULT_LANE = "default"
ORIGINS = ("marker", "coverage_verdict", "external")


@dataclass(frozen=True)
class CaseLink:
    nodeid: str
    requirements: tuple[str, ...]
    origin: str = "marker"
    case_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "nodeid": self.nodeid,
            "case_id": self.case_id,
            "requirements": list(self.requirements),
            "origin": self.origin,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> CaseLink:
        return cls(
            nodeid=str(data["nodeid"]),
            requirements=tuple(str(r) for r in data.get("requirements") or ()),
            origin=str(data.get("origin") or "marker"),
            case_id=data.get("case_id"),
        )


@dataclass(frozen=True)
class InvalidLink:
    nodeid: str
    id: str
    problem: str  # format | prefix | unknown | retired

    def to_json(self) -> dict[str, str]:
        return {"nodeid": self.nodeid, "id": self.id, "problem": self.problem}


@dataclass
class CaseLinks:
    """One case-links document (or several merged)."""

    run_id: str = ""
    links: list[CaseLink] = field(default_factory=list)
    invalid: list[InvalidLink] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    tests: dict[str, dict[str, Any]] = field(default_factory=dict)

    def requirements_of(self, nodeid: str) -> set[str]:
        return {r for link in self.links if link.nodeid == nodeid for r in link.requirements}

    def by_requirement(self) -> dict[str, list[CaseLink]]:
        out: dict[str, list[CaseLink]] = {}
        for link in self.links:
            for req in link.requirements:
                out.setdefault(req, []).append(link)
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "generated_at": utc_now(),
            "links": [link.to_json() for link in self.links],
            "invalid": [bad.to_json() for bad in self.invalid],
            "mismatches": list(self.mismatches),
            "tests": dict(self.tests),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> CaseLinks:
        return cls(
            run_id=str(data.get("run_id") or ""),
            links=[CaseLink.from_json(x) for x in data.get("links") or ()],
            invalid=[
                InvalidLink(str(x["nodeid"]), str(x["id"]), str(x["problem"]))
                for x in data.get("invalid") or ()
            ],
            mismatches=list(data.get("mismatches") or ()),
            tests={str(k): dict(v) for k, v in (data.get("tests") or {}).items()},
        )

    def merge(self, other: CaseLinks) -> CaseLinks:
        seen = {(link.nodeid, link.origin) for link in self.links}
        merged = CaseLinks(
            run_id=self.run_id or other.run_id,
            links=list(self.links) + [x for x in other.links if (x.nodeid, x.origin) not in seen],
            invalid=list(dict.fromkeys([*self.invalid, *other.invalid])),
            mismatches=[*self.mismatches, *other.mismatches],
            tests={**other.tests, **self.tests},
        )
        return merged


def links_path(directory: str | Path | None = None, lane: str = DEFAULT_LANE) -> Path:
    base = Path(directory) if directory is not None else trace_dir()
    suffix = "" if lane in ("", DEFAULT_LANE) else f"-{lane}"
    return base / f"{FILE_STEM}{suffix}.json"


def write_case_links(doc: CaseLinks, path: Path) -> Path:
    data = doc.to_json()
    validate(data, "case-links.schema.json")
    return write_json(path, data)


def read_case_links(path: str | Path) -> list[CaseLink]:
    """The links in one file (an absent or unreadable file has none)."""
    return load_case_links(path).links


def load_case_links(path: str | Path) -> CaseLinks:
    data = read_json(Path(path))
    return CaseLinks.from_json(data) if isinstance(data, dict) else CaseLinks()


def load_all(directory: str | Path) -> CaseLinks:
    """Every ``case-links*.json`` under ``directory`` (all lanes), merged."""
    merged = CaseLinks()
    for path in sorted(Path(directory).rglob(f"{FILE_STEM}*.json")):
        merged = merged.merge(load_case_links(path))
    return merged


def external_links(rows: Iterable[Mapping[str, Any]]) -> list[CaseLink]:
    """Links pushed by another framework: ``{"nodeid", "requirements": [...]}`` per result."""
    out = []
    for row in rows:
        reqs = tuple(str(r) for r in row.get("requirements") or ())
        if reqs:
            out.append(CaseLink(str(row["nodeid"]), reqs, "external", row.get("case_id") or None))
    return out
