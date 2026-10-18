"""Requirements sync: merge every source into one snapshot and report what changed.

``sync()`` fetches each source, merges them (first source wins on order; the same id with
different content from two sources is an error, never a silent pick), diffs the result
against the previous snapshot (or the current registry) and lists:

* added, retitled, changed (priority/status/text: linked tests need a re-review), retired,
  vanished;
* **orphaned links**: tests that point at a requirement that is now retired or gone.

``write=False`` (the default) is a dry run that writes nothing. With ``write=True`` the
snapshot ``requirements/snapshot.json`` is written; a scheduled job commits it through a
pull request so every requirement change is a reviewable diff.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traceability._util import utc_now, validate, write_json
from traceability.links import CaseLink
from traceability.requirements import Registry, Requirement

DEFAULT_SNAPSHOT = Path("requirements") / "snapshot.json"


class DuplicateRequirement(ValueError):
    """Two sources define the same id differently."""


def merge(*batches: tuple[str, Iterable[Requirement]]) -> list[Requirement]:
    """``(source name, requirements)`` batches in precedence order -> one list."""
    seen: dict[str, tuple[str, Requirement]] = {}
    for source, items in batches:
        for req in items:
            prior = seen.get(req.id)
            if prior is None:
                seen[req.id] = (source, req)
            elif (prior[1].title, prior[1].status, prior[1].priority) != (
                req.title,
                req.status,
                req.priority,
            ):
                raise DuplicateRequirement(
                    f"{req.id} is defined by {prior[0]} and {source} with different content"
                )
    return [r for _, r in sorted(seen.values(), key=lambda x: _key(x[1].id))]


def _key(req_id: str) -> tuple:
    head, _, tail = req_id.partition("-")
    return (head, int(tail)) if tail.isdigit() else (head, 0)


@dataclass
class SyncReport:
    sources: list[str]
    added: list[str] = field(default_factory=list)
    retitled: list[dict[str, str]] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    vanished: list[str] = field(default_factory=list)
    orphaned_links: list[dict[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    written: str | None = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""

    @property
    def empty(self) -> bool:
        return not (self.added or self.retitled or self.changed or self.retired or self.vanished)

    def to_json(self) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "added": self.added,
            "retitled": self.retitled,
            "changed": self.changed,
            "retired": self.retired,
            "vanished": self.vanished,
            "orphaned_links": self.orphaned_links,
            "skipped": self.skipped,
            "written": self.written,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def to_markdown(self) -> str:
        lines = [f"## Requirements sync ({', '.join(self.sources)})", ""]
        if self.empty:
            lines.append("No requirement changed.")
        for title, items in (
            ("Added", self.added),
            ("Changed (linked tests need a re-review)", self.changed),
            ("Retired", self.retired),
            ("Vanished from every source", self.vanished),
        ):
            if items:
                lines += ["", f"**{title}:** " + ", ".join(items)]
        if self.retitled:
            lines += ["", "**Retitled:**", ""]
            lines += [f"- {r['id']}: {r['old']} → {r['new']}" for r in self.retitled]
        if self.orphaned_links:
            lines += ["", "**Orphaned links** (tests pointing at a retired or vanished id):", ""]
            lines += [
                f"- `{o['nodeid']}` → {o['requirement_id']} ({o['why']})"
                for o in self.orphaned_links
            ]
        if self.skipped:
            lines += ["", "Skipped source records: " + "; ".join(self.skipped)]
        return "\n".join(lines) + "\n"


def snapshot_body(requirements: Sequence[Requirement], sources: Sequence[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "sources": list(sources),
        "requirements": [{**r.to_json(), "content_hash": r.content_hash} for r in requirements],
    }


def sync(
    sources: Sequence[Any],
    snapshot: Path,
    links: Iterable[CaseLink] = (),
    *,
    previous: Registry | None = None,
    write: bool = False,
) -> SyncReport:
    """Fetch, merge, diff and (with ``write``) write the snapshot."""
    report = SyncReport([s.name for s in sources])
    batches = []
    for source in sources:
        batches.append((source.name, list(source.fetch())))
        report.skipped += [f"{source.name} {s}" for s in getattr(source, "skipped", [])]
    merged = merge(*batches)
    old = previous if previous is not None else Registry.load(snapshot)
    new = {r.id: r for r in merged}
    for req in merged:
        before = old.get(req.id)
        if before is None:
            report.added.append(req.id)
            continue
        if before.title != req.title:
            report.retitled.append({"id": req.id, "old": before.title, "new": req.title})
        if before.status == "active" and req.status == "retired":
            report.retired.append(req.id)
        elif before.content_hash != req.content_hash:
            report.changed.append(req.id)
    report.vanished = sorted((r.id for r in old if r.id not in new), key=_key)
    gone = {i: "vanished" for i in report.vanished}
    gone.update({r.id: "retired" for r in merged if r.status == "retired"})
    for link in links:
        for req_id in link.requirements:
            if req_id in gone:
                report.orphaned_links.append(
                    {"nodeid": link.nodeid, "requirement_id": req_id, "why": gone[req_id]}
                )
    if write:
        body = snapshot_body(merged, report.sources)
        validate(body, "requirements-snapshot.schema.json")
        write_json(snapshot, body)
        report.written = str(snapshot)
    report.finished_at = utc_now()
    return report
