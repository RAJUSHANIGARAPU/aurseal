"""Requirement ids written in prose must also be formal links.

A test whose name, docstring, class docstring or ``pytest.param(id=...)`` mentions
``REQ-7`` claims REQ-7 to a reader, but only a ``requirement`` marker makes the claim count
in the matrix. ``links --check-prose`` lists every prose id that is not among the test's
marker ids as ``<nodeid-without-params> <id>``.

A committed baseline (one ``<test> <id>`` per line, ``#`` comments) tolerates the
mentions that existed when the rule was added. It can only shrink: a violation not in it
fails, and so does a baseline line that is no longer a violation ("stale, delete it").
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traceability.requirements import Namespace

DEFAULT_BASELINE = Path("traceability") / "prose-baseline.txt"
_TEXT_ID = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")
_NAME_ID = re.compile(r"(?:^|_)([A-Za-z][A-Za-z0-9]*)_(\d+)(?=_|$)")


def ids_in_text(text: str | None, ns: Namespace) -> list[str]:
    found = [m for m in _TEXT_ID.findall(text or "") if ns.is_requirement_id(m)]
    return list(dict.fromkeys(found))


def ids_in_name(name: str, ns: Namespace) -> list[str]:
    """``test_req_7_totals`` -> ``REQ-7`` (snake case cannot hold a hyphen)."""
    found = []
    for head, number in _NAME_ID.findall(name):
        candidate = f"{head.upper()}-{number}"
        if ns.is_requirement_id(candidate):
            found.append(candidate)
    return list(dict.fromkeys(found))


def base_nodeid(nodeid: str) -> str:
    return nodeid.split("[", 1)[0]


@dataclass(frozen=True)
class ProseViolation:
    test: str
    id: str

    def __str__(self) -> str:
        return f"{self.test} {self.id}"


def violations(items: Iterable[Mapping[str, Any]]) -> list[ProseViolation]:
    """Prose ids that are not among the test's marker ids (collected items)."""
    out: set[ProseViolation] = set()
    for item in items:
        marked = set(item.get("requirements") or ()) | {b["id"] for b in item.get("invalid") or ()}
        for found in item.get("prose_ids") or ():
            if found not in marked:
                out.add(ProseViolation(base_nodeid(item["nodeid"]), found))
    return sorted(out, key=lambda v: (v.test, v.id))


def load_baseline(path: Path) -> set[str]:
    if not Path(path).is_file():
        return set()
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return {" ".join(x.split()) for x in lines if x.strip() and not x.lstrip().startswith("#")}


@dataclass
class ProseResult:
    new: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    tolerated: int = 0

    @property
    def ok(self) -> bool:
        return not self.new and not self.stale


def ratchet(found: Iterable[ProseViolation], baseline: set[str]) -> ProseResult:
    current = {str(v) for v in found}
    return ProseResult(
        sorted(current - baseline), sorted(baseline - current), len(current & baseline)
    )


def write_baseline(found: Iterable[ProseViolation], path: Path) -> Path:
    body = [
        "# Requirement ids mentioned in a test's prose but not linked with a marker.",
        "# Shrink-only: delete a line once the test carries the marker (or drops the mention).",
        *sorted(str(v) for v in found),
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(body) + "\n", encoding="utf-8")
    return Path(path)
