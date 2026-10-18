"""Open issues kept as data: one JSON manifest per family of templates, with gate-safety rails.

A suite that checks many similar templates (documents, screens, exports) against one set of
finding ids keeps what is known to be wrong next to the data rather than in code. Every
``*.manifest.json`` file holds a JSON list of entries::

    [{"template": "monthly-statement",
      "absent_from_envs": ["staging"],
      "samples": ["samples/monthly-statement.complete.json"],
      "open_issues": [
        {"issue": "BUG-142", "summary": "rounding", "notes": "totals off by a cent",
         "seen_in": "statement 2024-03, line 4", "finding": "TOTAL-MISMATCH"},
        {"issue": "BUG-150", "summary": "wording", "notes": "old footer text"}]}]

For an accessibility manifest the ``template`` is the route path of the screen.

**An open issue never makes a check pass.** ``finding`` decides what an issue does:

* with ``finding`` it is a *detector*: that finding id is excused from the main check and
  asserted to keep occurring. When it stops, the entry reports ``cleared`` and the test
  fails, so the entry tells you it is out of date;
* without ``finding`` it is a *note*: quoted in the failure message, so a red check reads
  "known broken, tracked as BUG-150" instead of looking like a broken test. It excuses
  nothing.

Excusing is silent and asserting is not, so the deal needs guards. :func:`check_manifest`
lists every way an issue could be excused without being asserted (a finding id nothing
emits, a detector whose oracle input is empty, a detector with no ``seen_in``, an
environment name that matches nothing) and fails once excused issues stop being a minority.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verification.verdicts import Kind, RedVerdict, record_verdict

MANIFEST_GLOB = "*.manifest.json"


@dataclass(frozen=True)
class OpenIssue:
    issue: str
    summary: str
    notes: str = ""
    seen_in: str = ""
    finding: str | None = None

    @property
    def is_detector(self) -> bool:
        """True when the issue is asserted, and so can report its own fix."""
        return bool(self.finding and self.finding.strip())

    def __str__(self) -> str:
        role = f" {{asserted as {self.finding}}}" if self.is_detector else " {note, not asserted}"
        return f"{self.issue} [{self.summary}] {self.notes} ({self.seen_in}){role}"


@dataclass(frozen=True)
class ManifestEntry:
    template: str
    open_issues: tuple[OpenIssue, ...] = ()
    absent_from_envs: tuple[str, ...] = ()
    samples: tuple[str, ...] = ()
    source: Path | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def open_issue_note(self) -> str:
        """The open issues, formatted for a failure message ("" when there are none)."""
        if not self.open_issues:
            return ""
        lines = [
            "",
            f"  Open issues on {self.template} (more likely the cause of a failure here than "
            f"the test):",
        ]
        lines += [f"    - {d}" for d in self.open_issues]
        return "\n".join(lines)


def load_manifest(location: str | Path, pattern: str = MANIFEST_GLOB) -> list[ManifestEntry]:
    """Entries from one manifest file, or from every ``pattern`` file in a directory.

    Files are found by pattern, so there is no list to keep up to date. A directory with no
    manifest raises: an empty manifest checks nothing.
    """
    location = Path(location)
    files = sorted(location.glob(pattern)) if location.is_dir() else [location]
    if not files:
        raise ValueError(f"no {pattern} under {location}: nothing to check")
    entries: list[ManifestEntry] = []
    for path in files:
        entries += parse_manifest(json.loads(path.read_text(encoding="utf-8")), source=path)
    return sorted(entries, key=lambda e: e.template.lower())


def parse_manifest(raw: object, source: Path | None = None) -> list[ManifestEntry]:
    where = str(source or "manifest")
    if not isinstance(raw, list):
        raise ValueError(f"{where} must hold a JSON list")
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{where}: every entry must be an object")
        template = str(item.get("template") or "").strip()
        if not template:
            raise ValueError(f"{where}: an entry has no 'template'")
        issues = tuple(
            OpenIssue(
                issue=str(d.get("issue") or ""),
                summary=str(d.get("summary") or ""),
                notes=str(d.get("notes") or ""),
                seen_in=str(d.get("seen_in") or ""),
                finding=d.get("finding") or None,
            )
            for d in item.get("open_issues") or ()
        )
        entries.append(
            ManifestEntry(
                template=template,
                open_issues=issues,
                absent_from_envs=tuple(str(e) for e in item.get("absent_from_envs") or ()),
                samples=tuple(str(f) for f in item.get("samples") or ()),
                source=source,
                data=item,
            )
        )
    return entries


def by_template(entries: Iterable[ManifestEntry], template: str) -> ManifestEntry | None:
    return next((e for e in entries if e.template == template), None)


@dataclass(frozen=True)
class TrackedVerdict:
    ticket: str
    verdict: RedVerdict
    detail: str


def tracked_defect_verdicts(
    template: str,
    issues: Iterable[OpenIssue],
    observed_kinds: Collection[str],
    observable_kinds: Collection[str],
) -> list[TrackedVerdict]:
    """Verdicts for the detectors on ``template`` that this observer was able to see.

    ``observable_kinds`` matters. ``cleared`` is read from a finding being ABSENT, and an
    observer that cannot emit a finding id reports it absent just as firmly as one that
    looked and found nothing. A detector outside the observer's reach gets no verdict:
    silence, not an invented "fixed". Notes are skipped because nothing observes them.
    """
    return [
        TrackedVerdict(
            d.issue,
            RedVerdict.REPRODUCES if d.finding in observed_kinds else RedVerdict.CLEARED,
            f"{d.finding} on {template!r} [{d.summary}]",
        )
        for d in issues
        if d.is_detector and d.finding in observable_kinds
    ]


def record_tracked(verdicts: Iterable[TrackedVerdict], nodeid: str | None = None) -> None:
    for v in verdicts:
        record_verdict(
            Kind.EXPECTED_RED, v.verdict, ticket=v.ticket, detail=v.detail, nodeid=nodeid
        )


@dataclass(frozen=True)
class FindingsJudgement:
    template: str
    unexplained: tuple[str, ...]
    verdicts: tuple[TrackedVerdict, ...]
    note: str = ""

    @property
    def cleared(self) -> tuple[TrackedVerdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict is RedVerdict.CLEARED)

    def assert_clean(self) -> None:
        """Fail on any finding no detector explains, and on any detector that cleared."""
        problems = []
        if self.unexplained:
            problems.append(f"{self.template}: finding(s) nobody explains {list(self.unexplained)}")
        problems += [
            f"KNOWN DEFECT CLEARED: {v.ticket} no longer reproduces ({v.detail}); check it, "
            f"close the issue and remove its 'finding' from the manifest"
            for v in self.cleared
        ]
        if problems:
            raise AssertionError("\n".join(problems) + self.note)


def judge_findings(
    entry: ManifestEntry,
    observed_kinds: Iterable[str],
    observable_kinds: Collection[str],
    *,
    record: bool = True,
    nodeid: str | None = None,
) -> FindingsJudgement:
    """Split one template's findings into excused-and-asserted and unexplained.

    Only a detector excuses its finding id, and only on its own template; a note never
    does. Call ``assert_clean()`` on the result to apply the verdict.
    """
    observed = list(dict.fromkeys(observed_kinds))
    excused = {d.finding for d in entry.open_issues if d.is_detector}
    verdicts = tracked_defect_verdicts(
        entry.template, entry.open_issues, set(observed), observable_kinds
    )
    if record:
        record_tracked(verdicts, nodeid=nodeid)
    return FindingsJudgement(
        template=entry.template,
        unexplained=tuple(k for k in observed if k not in excused),
        verdicts=tuple(verdicts),
        note=entry.open_issue_note(),
    )


def detector_split(entries: Iterable[ManifestEntry]) -> tuple[int, int]:
    """(asserted, noted) issue counts, so notes are never counted as coverage."""
    issues = [d for e in entries for d in e.open_issues]
    asserted = sum(1 for d in issues if d.is_detector)
    return asserted, len(issues) - asserted


def check_manifest(
    entries: Iterable[ManifestEntry],
    *,
    emitted_kinds: Collection[str] | None = None,
    known_envs: Collection[str] | None = None,
    oracle_inputs: Mapping[str, str] | None = None,
    max_excused_share: float = 0.5,
) -> list[str]:
    """Every consistency and gate-safety problem in the manifest ([] when sound).

    * a template named by two entries (they would disagree about it);
    * an issue without an ``issue`` id or ``summary``; a detector without ``seen_in`` (an
      excused finding nobody can trace looks the same as a hidden one);
    * a ``finding`` no check emits (``emitted_kinds``): excused from the main check and never
      asserted, so suppressed on both sides;
    * a detector whose oracle reads an entry field that is empty (``oracle_inputs`` maps
      finding id -> field name): the oracle can never fire, so it reports "cleared" on
      every run;
    * an ``absent_from_envs`` value outside ``known_envs``: it never matches, so the skip
      it declares never happens;
    * a sample that is missing or is not valid JSON;
    * excused detectors reaching ``max_excused_share`` of all open issues: that is a
      quarantine list, not a policy.
    """
    entries = list(entries)
    problems: list[str] = []
    seen: dict[str, str] = {}
    for e in entries:
        where = str(e.source or "manifest")
        if e.template in seen:
            problems.append(f"{e.template!r} has entries in both {seen[e.template]} and {where}")
        seen.setdefault(e.template, where)
        for d in e.open_issues:
            if not d.issue.strip() or not d.summary.strip():
                problems.append(
                    f"{e.template!r}: an open issue needs an issue id and a summary: {d}"
                )
            if d.is_detector and not d.seen_in.strip():
                problems.append(
                    f"{e.template!r}: detector {d.issue} does not say where it was seen"
                )
            if d.is_detector and emitted_kinds is not None and d.finding not in emitted_kinds:
                problems.append(
                    f"{e.template!r}: {d.issue} is asserted as {d.finding}, which no check "
                    f"emits (emitted: {sorted(emitted_kinds)})"
                )
            needs = (oracle_inputs or {}).get(d.finding or "")
            if d.is_detector and needs and not e.data.get(needs):
                problems.append(
                    f"{e.template!r}: {d.issue} relies on {d.finding}, whose oracle input "
                    f"{needs!r} is empty, so it can never fire"
                )
        if known_envs is not None:
            problems += [
                f"{e.template!r}: absent_from_envs {env!r} is not a known environment"
                for env in e.absent_from_envs
                if env not in known_envs
            ]
        problems += _sample_problems(e)
    asserted, noted = detector_split(entries)
    total = asserted + noted
    if total and asserted >= max(2, total * max_excused_share):
        problems.append(
            f"{asserted} of {total} open issues are excused from the main checks; the suite "
            f"would call the templates checked while skipping most of what it found"
        )
    return problems


def _sample_problems(entry: ManifestEntry) -> list[str]:
    if entry.source is None:
        return []
    problems = []
    for sample in entry.samples:
        path = entry.source.parent / sample
        if not path.is_file():
            problems.append(f"{entry.template!r}: sample {sample} does not exist")
        elif path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"{entry.template!r}: sample {sample} is not valid JSON: {exc}")
    return problems
