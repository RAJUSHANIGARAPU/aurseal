"""Data-health scanning: rules over read-only API listings, with an offline-testable analyzer.

A shared environment accumulates records a later test or user will trip over:
malformed timestamps, references to things that no longer exist, drafts left
open, jobs stuck in a failed state. The scan finds them before a test does.

Three pieces, kept apart so each is testable without an environment:

* :class:`GetOnlyApi` — the scan's only door to the environment: ``get``.
  Built with :meth:`GetOnlyApi.over` from an engine, it exposes nothing that
  writes, so the scan cannot change a shared environment by intent or by a
  later edit.
* :class:`Rule` + :func:`analyze` — pure functions over records → findings.
* :class:`Scanner` — pages through listings with hard caps; whenever a cap cuts
  the scan short the report says so, because a capped scan that reports
  "0 findings" is not a clean bill of health.
* :func:`windowed` — reads a listing in creation-date windows, oldest first, one page
  per window, for listings whose order is unstable under a filter or whose page-size cap
  hides older records. A window that reports more records than it returned is noted and
  the read marked incomplete; records on a window edge are counted once.

Built-in rule factories cover the common defect shapes; write your own as any
``(record) -> list[str]`` callable. Return :class:`Unverified` for a result the
scan could not establish: it lands in its own column, never as a finding.

:class:`ScanReport` renders Markdown and JSON with per-rule counts, the
unverified column, a creator split (the suite's own accounts vs everyone else
vs unknown), bounded samples, the requests sent and why a scan stopped.
:func:`main` is the opt-in entry point (``python -m oracles.data_health``),
writing ``reports/data-health/<env>.md``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from api.clients.http_engine import ApiResponse
from api.engine.context_memory import resolve_path

logger = logging.getLogger(__name__)

Record = Mapping[str, Any]


class _Gettable(Protocol):
    def get(self, path: str, params: Mapping[str, Any] | None = None) -> ApiResponse: ...


class GetOnlyApi:
    """GET only. Hold one of these, never an engine, in scan code."""

    __slots__ = ("_get",)

    def __init__(self, get: Callable[[str, Mapping[str, Any] | None], ApiResponse]) -> None:
        self._get = get

    @classmethod
    def over(cls, engine: _Gettable) -> GetOnlyApi:
        return cls(lambda path, params=None: engine.get(path, params=params))

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> ApiResponse:
        return self._get(path, params)


class Unverified(str):
    """A rule result the scan could not establish (for example, a referenced record that could
    not be read). Return it from a check instead of a plain string: it is counted in its own
    column, never as a finding and never as healthy."""


AUTOMATION = "automation"
OTHERS = "others"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Finding:
    rule: str
    record_id: str
    detail: str
    verified: bool = True
    created_by: str | None = None
    origin: str = UNKNOWN
    """Who made the record: ``automation`` (the suite's own accounts), ``others`` or ``unknown``."""
    entity_key: str = ""

    def __str__(self) -> str:
        mark = "" if self.verified else " (unverified)"
        return f"[{self.rule}] {self.record_id}: {self.detail}{mark}"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    check: Callable[[Record], list[str]]


def _origin(
    record: Record, creator: str | None, is_automation: Callable[[Record], bool] | None
) -> str:
    if is_automation is not None and is_automation(record):
        return AUTOMATION
    return OTHERS if creator else UNKNOWN


def analyze(
    records: Iterable[Record],
    rules: Iterable[Rule],
    id_of: Callable[[Record], str],
    *,
    created_by: Callable[[Record], str | None] | None = None,
    is_automation: Callable[[Record], bool] | None = None,
    entity_key: str = "",
) -> list[Finding]:
    """Run every rule over every record.

    ``created_by(record)`` names who made the record; ``is_automation(record)``
    says whether the suite's own accounts made it (see :func:`accounts_predicate`).
    Together they let a report tell harness-made records from everyone else's.
    """
    rules = list(rules)
    out = []
    for record in records:
        creator = None
        if created_by is not None:
            raw = created_by(record)
            creator = str(raw).strip() if raw not in (None, "") else None
        for rule in rules:
            for detail in rule.check(record):
                out.append(
                    Finding(
                        rule.id,
                        id_of(record),
                        str(detail),
                        verified=not isinstance(detail, Unverified),
                        created_by=creator,
                        origin=_origin(record, creator, is_automation),
                        entity_key=entity_key,
                    )
                )
    return out


def accounts_predicate(
    accounts: Iterable[str], created_by: Callable[[Record], str | None]
) -> Callable[[Record], bool]:
    """``is_automation``: the record was created by one of ``accounts`` (case-insensitive)."""
    names = {a.strip().casefold() for a in accounts if a.strip()}

    def is_automation(record: Record) -> bool:
        creator = created_by(record)
        return bool(creator) and str(creator).strip().casefold() in names

    return is_automation


def select_rules(rules: Iterable[Rule], spec: str) -> list[Rule]:
    """``all`` or a comma-separated list of rule ids; an unknown id fails."""
    rules = list(rules)
    if spec.strip().lower() in ("", "all"):
        return rules
    by_id = {r.id: r for r in rules}
    wanted = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        raise ValueError(f"unknown rule id(s) {unknown}; known: {sorted(by_id)}")
    return [by_id[w] for w in wanted]


# ---------------------------------------------------------------------------
# rule factories
# ---------------------------------------------------------------------------

_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?P<frac>\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _values(record: Record, paths: Iterable[str]) -> Iterable[tuple[str, Any]]:
    for p in paths:
        try:
            yield p, resolve_path(record, p)
        except (KeyError, IndexError, TypeError):
            continue


def datetime_precision(paths: Iterable[str], fraction_digits: int = 3) -> Rule:
    """Date-time fields whose fractional seconds are not exactly ``fraction_digits`` long.

    A consumer that parses with a fixed pattern rejects anything else, so the
    record breaks the *next* operation that reads it, not the one that wrote it.
    """
    paths = list(paths)

    def check(record: Record) -> list[str]:
        out = []
        for p, v in _values(record, paths):
            if not isinstance(v, str) or _DATE_ONLY.match(v):
                continue
            m = _DATETIME.match(v)
            frac = len(m.group("frac") or ".") - 1 if m else None
            if m is None or frac != fraction_digits:
                out.append(f"{p}={v!r} is not a date-time with {fraction_digits} fraction digits")
        return out

    return Rule("datetime-precision", "Date-times with the wrong precision", check)


def date_without_time(paths: Iterable[str]) -> Rule:
    """A date-time field holding a bare date."""
    paths = list(paths)

    def check(record: Record) -> list[str]:
        return [
            f"{p}={v!r} is a bare date in a date-time field"
            for p, v in _values(record, paths)
            if isinstance(v, str) and _DATE_ONLY.match(v)
        ]

    return Rule("date-without-time", "Timestamp fields that carry only a date", check)


def status_in(path: str, bad: Iterable[str], rule_id: str = "bad-status") -> Rule:
    bad_set = set(bad)

    def check(record: Record) -> list[str]:
        return [f"{p}={v}" for p, v in _values(record, [path]) if v in bad_set]

    return Rule(rule_id, f"{path} in {sorted(bad_set)}", check)


def required_when(path: str, equals: Any, required: Iterable[str], rule_id: str) -> Rule:
    """When ``path == equals``, every ``required`` path must be non-blank."""
    required = list(required)

    def check(record: Record) -> list[str]:
        if dict(_values(record, [path])).get(path) != equals:
            return []
        present = dict(_values(record, required))
        return [f"{r} is blank" for r in required if present.get(r) in (None, "")]

    return Rule(rule_id, f"{required} required when {path}={equals!r}", check)


def uuid7_time(value: str) -> datetime | None:
    """Creation time embedded in a UUIDv7 (first 48 bits, ms since epoch); None otherwise."""
    try:
        u = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    if u.version != 7:
        return None
    return datetime.fromtimestamp((u.int >> 80) / 1000, tz=timezone.utc)


def stale_by_uuid7(path: str, max_age: timedelta, now: datetime | None = None) -> Rule:
    """Records whose UUIDv7 id at ``path`` was created more than ``max_age`` ago."""

    def check(record: Record) -> list[str]:
        current = now or datetime.now(timezone.utc)
        for p, v in _values(record, [path]):
            created = uuid7_time(str(v)) if v else None
            if created is not None and current - created > max_age:
                return [f"{p} created {created.isoformat()} — older than {max_age}"]
        return []

    return Rule("stale", f"{path} older than {max_age}", check)


# ---------------------------------------------------------------------------
# scanner
# ---------------------------------------------------------------------------


@dataclass
class RuleSummary:
    """One rule's outcome: findings and unverified results counted apart, split by creator."""

    count: int = 0
    unverified: int = 0
    by_automation: int = 0
    by_others: int = 0
    creator_unknown: int = 0
    creators: dict[str, int] = field(default_factory=dict)
    samples: list[Finding] = field(default_factory=list)
    unverified_samples: list[Finding] = field(default_factory=list)

    def top_creators(self, n: int = 5) -> list[tuple[str, int]]:
        return sorted(self.creators.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)
    scanned: int = 0
    notes: list[str] = field(default_factory=list)
    """Why the scan is incomplete (a page cap, a refused read)."""
    requests: int = 0
    """GET requests sent: the scan's cost on the environment."""
    abort_reason: str | None = None
    """Why the scan stopped before finishing. A stopped scan is not a result."""
    scanned_per_key: dict[str, int] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    automation_accounts: tuple[str, ...] = ()
    sample_limit: int = 5

    @property
    def complete(self) -> bool:
        """False when any cap, read failure or abort cut the scan short."""
        return not self.notes and self.abort_reason is None

    def abort(self, reason: str) -> None:
        if self.abort_reason is None:
            self.abort_reason = reason

    def by_rule(self) -> dict[str, RuleSummary]:
        """Per rule id (every rule scanned, findings or not), in rule order."""
        out: dict[str, RuleSummary] = {r.id: RuleSummary() for r in self.rules}
        for f in self.findings:
            summary = out.setdefault(f.rule, RuleSummary())
            if not f.verified:
                summary.unverified += 1
                if len(summary.unverified_samples) < self.sample_limit:
                    summary.unverified_samples.append(f)
                continue
            summary.count += 1
            if f.origin == AUTOMATION:
                summary.by_automation += 1
            elif f.origin == OTHERS:
                summary.by_others += 1
            else:
                summary.creator_unknown += 1
            if f.created_by:
                summary.creators[f.created_by] = summary.creators.get(f.created_by, 0) + 1
            if len(summary.samples) < self.sample_limit:
                summary.samples.append(f)
        return out

    def describe(self) -> str:
        verified = sum(f.verified for f in self.findings)
        head = f"{self.scanned} record(s) scanned, {verified} finding(s)"
        unverified = len(self.findings) - verified
        if unverified:
            head += f", {unverified} unverified"
        if self.abort_reason:
            head += " — STOPPED: " + self.abort_reason
        if self.notes:
            head += " — INCOMPLETE: " + "; ".join(self.notes)
        return "\n".join([head, *(f"  {f}" for f in self.findings)])

    def to_dict(self, environment: str = "", at: datetime | None = None) -> dict[str, Any]:
        return {
            "environment": environment,
            "scanned_at": (at or datetime.now(timezone.utc)).isoformat(),
            "complete": self.complete,
            "abort_reason": self.abort_reason,
            "notes": list(self.notes),
            "requests": self.requests,
            "scanned": self.scanned,
            "scanned_per_key": dict(self.scanned_per_key),
            "automation_accounts": sorted(self.automation_accounts),
            "rules": {
                rule_id: {
                    "count": s.count,
                    "unverified": s.unverified,
                    "by_automation": s.by_automation,
                    "by_others": s.by_others,
                    "creator_unknown": s.creator_unknown,
                    "top_creators": [list(c) for c in s.top_creators()],
                    "samples": [_finding_dict(f) for f in s.samples],
                    "unverified_samples": [_finding_dict(f) for f in s.unverified_samples],
                }
                for rule_id, s in self.by_rule().items()
            },
        }

    def to_markdown(self, environment: str, at: datetime | None = None) -> str:
        at = at or datetime.now(timezone.utc)
        titles = {r.id: r.title for r in self.rules}
        md = [f"# Data health — {environment}", ""]
        md.append(f"Scanned at {at.isoformat()}. Read-only (GET only), {self.requests} requests.")
        md.append("")
        if self.abort_reason:
            md += [
                "**SCAN ABORTED — partial report, do not treat as a result:** "
                + _cell(self.abort_reason),
                "",
            ]
        for note in self.notes:
            md.append(f"- INCOMPLETE: {note}")
        if self.notes:
            md.append("")
        md += ["| Entity key | Records read |", "|---|---|"]
        md += [f"| {_cell(k)} | {n} |" for k, n in self.scanned_per_key.items()]
        md += [
            "",
            "| Rule | Findings | Unverified | On automation-made records | On others' records "
            "| Creator unknown |",
            "|---|---|---|---|---|---|",
        ]
        summaries = self.by_rule()
        for rule_id, s in summaries.items():
            md.append(
                f"| {_cell(rule_id)} | {s.count} | {s.unverified} | {s.by_automation} "
                f"| {s.by_others} | {s.creator_unknown} |"
            )
        accounts = ", ".join(sorted(self.automation_accounts)) or "(none configured)"
        md += ["", f"Automation accounts: {accounts}"]
        for rule_id, s in summaries.items():
            md += ["", f"## {titles.get(rule_id, rule_id)} (`{rule_id}`)", ""]
            line = f"Findings: **{s.count}**"
            if s.unverified:
                line += f", unverified: **{s.unverified}**"
            md.append(line)
            if s.count:
                top = ", ".join(f"{c} ({n})" for c, n in s.top_creators()) or "none recorded"
                md += [
                    "",
                    f"By creator: automation {s.by_automation}, others {s.by_others}, "
                    f"unknown {s.creator_unknown}. Top creators: {top}",
                ]
            md += _samples("Sample", s.count, s.samples)
            md += _samples("Unverified sample", s.unverified, s.unverified_samples)
        return "\n".join(md) + "\n"


def _finding_dict(f: Finding) -> dict[str, Any]:
    return {
        "entity_key": f.entity_key,
        "record_id": f.record_id,
        "detail": f.detail,
        "created_by": f.created_by,
        "origin": f.origin,
    }


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _samples(label: str, total: int, samples: list[Finding]) -> list[str]:
    if not samples:
        return []
    out = ["", f"{label} ({len(samples)} of {total}):", "", "| Entity | Reference | Detail |"]
    out.append("|---|---|---|")
    out += [
        f"| {_cell(f.entity_key)} | {_cell(f.record_id)} | {_cell(f.detail)} |" for f in samples
    ]
    return out


#: Statuses after which the scan stops at once: the environment is failing or refusing us,
#: and reading on would only add load or report a login problem as data.
ABORT_STATUSES = frozenset({401, 403})


@dataclass
class Scanner:
    api: GetOnlyApi
    rules: list[Rule]
    id_of: Callable[[Record], str] = lambda r: str(r.get("id", "?"))
    items_path: str = "items"
    page_param: str = "page"
    size_param: str = "size"
    page_size: int = 100
    max_pages: int = 10
    created_by: Callable[[Record], str | None] | None = None
    is_automation: Callable[[Record], bool] | None = None
    automation_accounts: tuple[str, ...] = ()
    sample_limit: int = 5

    def new_report(self) -> ScanReport:
        return ScanReport(
            rules=list(self.rules),
            automation_accounts=tuple(self.automation_accounts),
            sample_limit=self.sample_limit,
        )

    def scan(
        self, listing_path: str, *, entity_key: str = "", report: ScanReport | None = None
    ) -> ScanReport:
        report = report if report is not None else self.new_report()
        key = entity_key or listing_path
        report.scanned_per_key.setdefault(key, 0)
        for page in range(self.max_pages):
            report.requests += 1
            response = self.api.get(
                listing_path, {self.page_param: page, self.size_param: self.page_size}
            )
            if not response.is_success:
                reason = f"{listing_path} page {page}: HTTP {response.status}"
                report.notes.append(reason)
                if response.status >= 500 or response.status in ABORT_STATUSES or page == 0:
                    report.abort(reason)
                return report
            try:
                body = response.json()
                items = resolve_path(body, self.items_path) if self.items_path else body
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                reason = f"{listing_path} page {page}: unreadable listing ({exc})"
                report.notes.append(reason)
                report.abort(reason)
                return report
            report.scanned += len(items)
            report.scanned_per_key[key] += len(items)
            report.findings.extend(
                analyze(
                    items,
                    self.rules,
                    self.id_of,
                    created_by=self.created_by,
                    is_automation=self.is_automation,
                    entity_key=key,
                )
            )
            if len(items) < self.page_size:
                return report
        report.notes.append(f"{listing_path}: capped at {self.max_pages} page(s)")
        logger.warning("data-health scan of %s capped at %d pages", listing_path, self.max_pages)
        return report

    def scan_all(self, listings: Mapping[str, str]) -> ScanReport:
        """Scan every ``entity key -> listing path`` into one report; stop at the first abort."""
        report = self.new_report()
        for key, path in listings.items():
            self.scan(path, entity_key=key, report=report)
            if report.abort_reason:
                break
        return report


# ---------------------------------------------------------------------------
# windowed paging
# ---------------------------------------------------------------------------


@dataclass
class WindowedListing:
    rows: list[Record] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """Why the read is incomplete (a truncated window, a refused read)."""
    requests: int = 0
    windows: int = 0

    @property
    def complete(self) -> bool:
        return not self.notes


def windowed(
    get: Callable[[str, Mapping[str, Any] | None], ApiResponse] | GetOnlyApi,
    path: str,
    *,
    start: datetime,
    end: datetime,
    window: timedelta,
    from_param: str,
    to_param: str,
    total_of: Callable[[Any], int | None],
    page_size: int,
    items_path: str = "items",
    size_param: str = "size",
    id_of: Callable[[Record], str] = lambda r: str(r.get("id", "?")),
    report: ScanReport | None = None,
) -> WindowedListing:
    """Read ``path`` in ``[t, t + window)`` slices from ``start`` to ``end``, one page each.

    ``total_of(body)`` is the record count the window reports (None when the listing
    does not say); more than the page returned means the window was truncated: noted,
    and the read is incomplete, so narrow the window rather than trust the result. Rows
    are de-duplicated by ``id_of`` across window edges. With ``report`` the notes and
    the request count are added to that scan report too.
    """
    if window <= timedelta(0):
        raise ValueError("windowed needs a positive window")
    fetch = get.get if isinstance(get, GetOnlyApi) else get
    listing = WindowedListing()
    seen: set[str] = set()
    t = start
    while t < end:
        upper = min(t + window, end)
        params = {from_param: t.isoformat(), to_param: upper.isoformat(), size_param: page_size}
        listing.requests += 1
        listing.windows += 1
        label = f"{path} [{t.isoformat()}, {upper.isoformat()})"
        response = fetch(path, params)
        if not response.is_success:
            listing.notes.append(f"{label}: HTTP {response.status}; later windows not read")
            break
        try:
            body = response.json()
            items = resolve_path(body, items_path) if items_path else body
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            listing.notes.append(f"{label}: unreadable listing ({exc}); later windows not read")
            break
        total = total_of(body)
        if total is not None and total > len(items):
            note = f"{label}: reports {total} record(s), read {len(items)}; narrow the window"
            listing.notes.append(note)
            logger.warning("data-health windowed read truncated: %s", note)
        for item in items:
            key = id_of(item)
            if key not in seen:
                seen.add(key)
                listing.rows.append(item)
        t = upper
    if report is not None:
        report.requests += listing.requests
        report.notes.extend(listing.notes)
    return listing


# ---------------------------------------------------------------------------
# runnable opt-in entry point
# ---------------------------------------------------------------------------

REPORT_DIR = Path("reports") / "data-health"

KEYS_VAR = "DATA_HEALTH_KEYS"
RULES_VAR = "DATA_HEALTH_RULES"
FAIL_ON_VAR = "DATA_HEALTH_FAIL_ON"
MAX_PAGES_VAR = "DATA_HEALTH_MAX_PAGES"
PAGE_SIZE_VAR = "DATA_HEALTH_PAGE_SIZE"
ACCOUNTS_VAR = "DATA_HEALTH_AUTOMATION_ACCOUNTS"
CREATOR_FIELD_VAR = "DATA_HEALTH_CREATOR_FIELD"
BASE_URL_VAR = "DATA_HEALTH_BASE_URL"
TOKEN_VAR = "DATA_HEALTH_TOKEN"
ENV_VAR = "TEST_ENV"

EXIT_OK = 0
EXIT_GATED = 1
EXIT_STOPPED = 2


def _split_list(raw: str | None) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def parse_keys(raw: str | None) -> dict[str, str]:
    """``orders=api/orders,invoices`` -> ``{"orders": "api/orders", "invoices": "invoices"}``."""
    keys: dict[str, str] = {}
    for token in _split_list(raw):
        key, sep, path = token.partition("=")
        keys[key.strip()] = path.strip() if sep and path.strip() else key.strip()
    return keys


@dataclass(frozen=True)
class ScanSettings:
    """What one scan reads and what gates it, resolved from ``DATA_HEALTH_*`` variables."""

    environment: str
    keys: dict[str, str]
    rule_spec: str = "all"
    fail_on: tuple[str, ...] = ()
    max_pages: int = 10
    page_size: int = 100
    automation_accounts: tuple[str, ...] = ()
    creator_field: str | None = "created_by"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ScanSettings:
        env = os.environ if environ is None else environ
        keys = parse_keys(env.get(KEYS_VAR))
        if not keys:
            raise ValueError(f"{KEYS_VAR} names no entity key (e.g. orders=api/orders,invoices)")
        return cls(
            environment=env.get(ENV_VAR, "qa").strip() or "qa",
            keys=keys,
            rule_spec=env.get(RULES_VAR, "all"),
            fail_on=tuple(_split_list(env.get(FAIL_ON_VAR))),
            max_pages=int(env.get(MAX_PAGES_VAR, "10")),
            page_size=int(env.get(PAGE_SIZE_VAR, "100")),
            automation_accounts=tuple(_split_list(env.get(ACCOUNTS_VAR))),
            creator_field=(env.get(CREATOR_FIELD_VAR, "created_by").strip() or None),
        )


def gated_counts(report: ScanReport, fail_on: Iterable[str]) -> dict[str, int]:
    """Rules named in ``fail_on`` (or ``all``) that have verified findings."""
    wanted = list(fail_on)
    summaries = report.by_rule()
    ids = list(summaries) if any(w.lower() == "all" for w in wanted) else wanted
    return {i: summaries[i].count for i in ids if i in summaries and summaries[i].count}


def run_scan(api: GetOnlyApi, rules: Iterable[Rule], settings: ScanSettings) -> ScanReport:
    """Scan every configured entity key read-only and return one report."""
    rules = select_rules(rules, settings.rule_spec)
    unknown = [r for r in settings.fail_on if r.lower() != "all" and r not in {x.id for x in rules}]
    if unknown:
        raise ValueError(f"{FAIL_ON_VAR} names rule(s) not scanned: {unknown}")
    field_name = settings.creator_field

    def creator(record: Record) -> str | None:
        if not field_name:
            return None
        try:
            value = resolve_path(record, field_name)
        except (KeyError, IndexError, TypeError):
            return None
        return None if value in (None, "") else str(value)

    scanner = Scanner(
        api,
        rules,
        page_size=settings.page_size,
        max_pages=settings.max_pages,
        created_by=creator if field_name else None,
        is_automation=accounts_predicate(settings.automation_accounts, creator)
        if settings.automation_accounts
        else None,
        automation_accounts=settings.automation_accounts,
    )
    return scanner.scan_all(settings.keys)


def write_markdown_report(
    report: ScanReport,
    environment: str,
    directory: Path | str = REPORT_DIR,
    at: datetime | None = None,
) -> Path:
    """Write ``<environment>.md`` and ``<environment>.json``; attach the Markdown to Allure."""
    at = at or datetime.now(timezone.utc)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    markdown = report.to_markdown(environment, at)
    md_path = out / f"{environment}.md"
    md_path.write_text(markdown, encoding="utf-8")
    (out / f"{environment}.json").write_text(
        json.dumps(report.to_dict(environment, at), indent=2), encoding="utf-8"
    )
    try:
        import allure
        from allure_commons.types import AttachmentType

        allure.attach(
            markdown, name=f"data-health-{environment}", attachment_type=AttachmentType.TEXT
        )
    except ImportError:  # pragma: no cover - allure is a project dependency
        pass
    return md_path


def main(
    argv: list[str] | None = None,
    *,
    rules: Iterable[Rule] | None = None,
    api: GetOnlyApi | None = None,
    environ: Mapping[str, str] | None = None,
    report_dir: Path | str = REPORT_DIR,
) -> int:
    """Run an opt-in, read-only scan. Exit 0 when it ran, 1 when a gated rule has findings,
    2 when the scan stopped (a stopped scan is not a result).

    Findings are reported, not asserted: they sit on shared data someone else's flow
    created. Name a rule in ``DATA_HEALTH_FAIL_ON`` to make it gate deliberately.
    ``rules`` default to :func:`default_rules`; ``api`` defaults to an engine over
    ``DATA_HEALTH_BASE_URL`` with ``DATA_HEALTH_TOKEN``, wrapped in :class:`GetOnlyApi`.
    """
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="python -m oracles.data_health", description=main.__doc__)
    parser.add_argument("--env", help=f"environment name (default {ENV_VAR} or qa)")
    parser.add_argument("--keys", help=f"entity keys, overrides {KEYS_VAR}")
    parser.add_argument("--fail-on", help=f"rule ids that gate, overrides {FAIL_ON_VAR}")
    args = parser.parse_args(argv)
    overrides = dict(env)
    for name, value in ((ENV_VAR, args.env), (KEYS_VAR, args.keys), (FAIL_ON_VAR, args.fail_on)):
        if value is not None:
            overrides[name] = value
    settings = ScanSettings.from_env(overrides)
    if api is None:
        from api.clients.http_engine import HttpEngine

        base_url = overrides.get(BASE_URL_VAR, "").strip()
        if not base_url:
            raise SystemExit(f"{BASE_URL_VAR} is not set")
        api = GetOnlyApi.over(HttpEngine(base_url, token=overrides.get(TOKEN_VAR) or None))
    report = run_scan(api, rules if rules is not None else default_rules(overrides), settings)
    path = write_markdown_report(report, settings.environment, report_dir)
    logger.info("[data-health] %s: %s; report %s", settings.environment, report.describe(), path)
    if report.abort_reason:
        return EXIT_STOPPED
    return EXIT_GATED if gated_counts(report, settings.fail_on) else EXIT_OK


def default_rules(environ: Mapping[str, str] | None = None) -> list[Rule]:
    """Built-in rules switched on by configuration; an unconfigured run has none and fails.

    * ``DATA_HEALTH_DATETIME_FIELDS`` — date-time precision and bare dates in those fields
      (``DATA_HEALTH_FRACTION_DIGITS``, default 3);
    * ``DATA_HEALTH_BAD_STATUS`` — ``path=VALUE|VALUE`` reported as ``bad-status``;
    * ``DATA_HEALTH_DRAFT_FIELD`` — UUIDv7 drafts older than ``DATA_HEALTH_DRAFT_MAX_AGE_HOURS``
      (default 48) reported as ``stale``.
    """
    env = os.environ if environ is None else environ
    rules: list[Rule] = []
    fields = _split_list(env.get("DATA_HEALTH_DATETIME_FIELDS"))
    if fields:
        digits = int(env.get("DATA_HEALTH_FRACTION_DIGITS", "3"))
        rules += [datetime_precision(fields, digits), date_without_time(fields)]
    bad = env.get("DATA_HEALTH_BAD_STATUS", "").strip()
    if bad:
        path, _, values = bad.partition("=")
        rules.append(status_in(path.strip(), [v.strip() for v in values.split("|") if v.strip()]))
    draft = env.get("DATA_HEALTH_DRAFT_FIELD", "").strip()
    if draft:
        hours = float(env.get("DATA_HEALTH_DRAFT_MAX_AGE_HOURS", "48"))
        rules.append(stale_by_uuid7(draft, timedelta(hours=hours)))
    if not rules:
        raise ValueError(
            "no data-health rule configured: set DATA_HEALTH_DATETIME_FIELDS, "
            "DATA_HEALTH_BAD_STATUS or DATA_HEALTH_DRAFT_FIELD, or pass rules="
        )
    return rules


if __name__ == "__main__":  # pragma: no cover - command-line entry
    raise SystemExit(main())
