"""How long a merged change takes to reach a release tag and then production.

For every change on a service's mainline (each first-parent commit: a merge
commit, a squash merge or a direct commit) the report takes three timestamps:

* ``merged_at``: the commit date of the change on mainline;
* ``tagged_at``: the date of the first release tag (by tag date) that contains it;
* ``deployed_at``: the first production deployment, in the deployment log, of a
  tag that contains it. Normally that is the first tag; when that tag never
  reached production, the next tag that did carries the change there.

and reports the median and p90 of merge->tag, tag->prod and merge->prod per
service and overall, over a ``--since``/``--until`` window, plus the slowest
changes. A change not tagged yet, or tagged but not deployed yet, is counted
as pending and kept out of the percentiles.

Without a deployment log, or when the log holds no production row for a
service, the production stages are NOT MEASURED, never zero.

The output is a pure function of the clones and the inputs: nothing is read
from the clock, so a rerun over the same refs gives the same report.

Exit status: 0 report written; 1 bad input.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tools.hotfix_mergeback.hotfix_mergeback import resolve_mainline
from tools.promotion_diff.promotion_diff import (
    DEFAULT_KEY_PATTERN,
    Git,
    InputError,
    _md,
    keys_in,
    load_pins,
)

STAGES = ("merge_to_tag", "tag_to_prod", "merge_to_prod")
STAGE_LABELS = {
    "merge_to_tag": "merge -> tag",
    "tag_to_prod": "tag -> prod",
    "merge_to_prod": "merge -> prod",
}
NOT_MEASURED = "NOT MEASURED"


def parse_time(value: str, where: str) -> datetime:
    """ISO 8601; a value without an offset is taken as UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise InputError(f"{where}: not an ISO 8601 time: {value!r}") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Deployment:
    tag: str
    environment: str
    deployed_at: datetime
    service: str | None = None  # None: the row applies to every service with that tag


def load_deployments(path: str | Path) -> list[Deployment]:
    """CSV with a header, or JSON (a list, or ``{"deployments": [...]}``).

    Fields: ``tag``, ``environment``, ``deployed_at`` and an optional ``service``.
    """
    source = Path(path)
    if not source.is_file():
        raise InputError(f"deployment log not found: {source}")
    text = source.read_text(encoding="utf-8")
    if source.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"{source}: not valid JSON ({exc})") from None
        if isinstance(data, dict):
            data = data.get("deployments")
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise InputError(f"{source}: expected a list of deployment objects")
        rows = data
    else:
        rows = list(csv.DictReader(text.splitlines()))
    result = []
    for number, row in enumerate(rows, 1):
        where = f"{source} row {number}"
        missing = [k for k in ("tag", "environment", "deployed_at") if not str(row.get(k) or "")]
        if missing:
            raise InputError(f"{where}: missing {', '.join(missing)}")
        service = str(row.get("service") or "").strip() or None
        result.append(
            Deployment(
                str(row["tag"]).strip(),
                str(row["environment"]).strip(),
                parse_time(str(row["deployed_at"]), where),
                service,
            )
        )
    return result


def parse_service(value: str) -> tuple[str, str]:
    name, _, repo = value.partition("=")
    if not name.strip():
        raise InputError(f"--service {value!r}: no name")
    return name.strip(), (repo.strip() or name.strip())


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tag:
    name: str
    sha: str
    at: datetime


@dataclass
class Change:
    sha: str
    subject: str
    merged_at: datetime
    tag: Tag | None = None
    deployed_tag: str | None = None
    deployed_at: datetime | None = None

    def hours(self, stage: str) -> float | None:
        start, end = {
            "merge_to_tag": (self.merged_at, self.tag and self.tag.at),
            "tag_to_prod": (self.tag and self.tag.at, self.deployed_at),
            "merge_to_prod": (self.merged_at, self.deployed_at),
        }[stage]
        if start is None or end is None:
            return None
        return (end - start).total_seconds() / 3600


def mainline_changes(git: Git, mainline: str) -> list[Change]:
    out = git.out("log", "--first-parent", "--format=%H%x1f%cI%x1f%s", mainline)
    changes = []
    for line in out.splitlines():
        sha, at, subject = line.split("\x1f", 2)
        changes.append(Change(sha, subject, parse_time(at, sha)))
    return changes


def release_tags(git: Git, patterns: Sequence[str]) -> list[Tag]:
    """Tags matching the globs, oldest first by tag date (commit date for a light tag)."""
    out = git.out(
        "for-each-ref",
        "--format=%(refname:short)%1f%(objectname)%1f%(*objectname)%1f%(creatordate:iso-strict)",
        *(f"refs/tags/{p}" for p in patterns),
    )
    tags = []
    for line in out.splitlines():
        name, obj, peeled, at = line.split("\x1f")
        tags.append(Tag(name, peeled or obj, parse_time(at, name)))
    return sorted(tags, key=lambda t: (t.at, t.name))


def first_tag_per_commit(git: Git, tags: Sequence[Tag]) -> dict[str, list[Tag]]:
    """Every commit reachable from a tag -> the tags containing it, oldest first."""
    containing: dict[str, list[Tag]] = {}
    for tag in tags:
        for sha in git.out("rev-list", tag.sha).split():
            containing.setdefault(sha, []).append(tag)
    return containing


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


@dataclass
class ServiceTimes:
    service: str
    problem: str | None = None
    changes: list[Change] = field(default_factory=list)
    prod_measured: bool = False
    skipped_tags: set[str] = field(default_factory=set)  # deployed before tagged: data error


def production_deployments(
    service: str, deployments: Sequence[Deployment], production: str
) -> dict[str, list[datetime]]:
    """Tag -> its production deployment times for this service, oldest first."""
    times: dict[str, list[datetime]] = {}
    for d in deployments:
        if d.environment == production and d.service in (None, service):
            times.setdefault(d.tag, []).append(d.deployed_at)
    return {tag: sorted(at) for tag, at in times.items()}


def analyse_service(
    workspace: Path,
    service: str,
    repo: str,
    mainline: str,
    tag_globs: Sequence[str],
    deployments: Sequence[Deployment] | None,
    production: str,
    window: tuple[datetime | None, datetime | None],
) -> ServiceTimes:
    times = ServiceTimes(service)
    git = Git(workspace / repo)
    if not git.exists():
        times.problem = f"no git clone at {workspace / repo}"
        return times
    main_sha = resolve_mainline(git, mainline)
    if main_sha is None:
        times.problem = f"mainline {mainline!r} not found"
        return times
    tags = release_tags(git, tag_globs)
    containing = first_tag_per_commit(git, tags)
    deployed = (
        production_deployments(service, deployments, production) if deployments is not None else {}
    )
    times.prod_measured = bool(deployed)
    since, until = window
    for change in mainline_changes(git, main_sha):
        if (since and change.merged_at < since) or (until and change.merged_at >= until):
            continue
        carriers = containing.get(change.sha, [])
        change.tag = carriers[0] if carriers else None
        candidates = []
        for tag in carriers:
            at = deployed.get(tag.name, [])
            if at and at[0] < tag.at:
                times.skipped_tags.add(tag.name)
            valid = [moment for moment in at if moment >= tag.at]
            if valid:
                candidates.append((valid[0], tag.name))
        if candidates:
            change.deployed_at, change.deployed_tag = min(candidates)
        times.changes.append(change)
    return times


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest rank: the smallest value with at least ``q`` of the values at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def stage_stats(changes: Sequence[Change], stage: str, measured: bool) -> dict:
    if not measured:
        return {"status": NOT_MEASURED}
    values = [h for h in (c.hours(stage) for c in changes) if h is not None]
    pending = len(changes) - len(values)
    if not values:
        return {"status": "no data", "count": 0, "pending": pending}
    return {
        "status": "measured",
        "count": len(values),
        "pending": pending,
        "median_hours": round(statistics.median(values), 2),
        "p90_hours": round(percentile(values, 0.9), 2),
    }


def summarise(changes: Sequence[Change], prod_changes: Sequence[Change], prod: bool) -> dict:
    """``prod_changes``: the changes of the services whose production stages are measured."""
    return {
        "merge_to_tag": stage_stats(changes, "merge_to_tag", True),
        "tag_to_prod": stage_stats([c for c in prod_changes if c.tag], "tag_to_prod", prod),
        "merge_to_prod": stage_stats(prod_changes, "merge_to_prod", prod),
    }


def slowest(services: Sequence[ServiceTimes], top: int) -> list[tuple[str, Change, str]]:
    """Longest end-to-end first: merge->prod where measured, else merge->tag."""
    ranked = []
    for s in services:
        for c in s.changes:
            for stage in ("merge_to_prod", "merge_to_tag"):
                hours = c.hours(stage)
                if hours is not None:
                    ranked.append((hours, s.service, c, stage))
                    break
    ranked.sort(key=lambda r: (-r[0], r[1], r[2].sha))
    return [(service, change, stage) for _, service, change, stage in ranked[:top]]


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def fmt_hours(hours: float | None) -> str:
    if hours is None:
        return "-"
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


def fmt_stat(stat: dict) -> tuple[str, str, str]:
    if stat["status"] != "measured":
        return stat["status"], stat["status"], str(stat.get("count", "-"))
    return (
        fmt_hours(stat["median_hours"]),
        fmt_hours(stat["p90_hours"]),
        f"{stat['count']} (+{stat['pending']} pending)" if stat["pending"] else str(stat["count"]),
    )


def report_data(
    services: Sequence[ServiceTimes],
    top: int,
    key_re: re.Pattern[str],
    window: tuple[datetime | None, datetime | None],
    production: str,
) -> dict:
    measured = [s for s in services if not s.problem]
    with_prod = [s for s in measured if s.prod_measured]
    return {
        "window": {
            "since": window[0] and window[0].isoformat(),
            "until": window[1] and window[1].isoformat(),
        },
        "production": production,
        "overall": summarise(
            [c for s in measured for c in s.changes],
            [c for s in with_prod for c in s.changes],
            bool(with_prod),
        ),
        "overall_prod_services": [s.service for s in with_prod],
        "services": [
            {
                "service": s.service,
                "problem": s.problem,
                "changes": len(s.changes),
                "untagged": sum(1 for c in s.changes if c.tag is None),
                "deployed_before_tagged": sorted(s.skipped_tags),
                "stages": None if s.problem else summarise(s.changes, s.changes, s.prod_measured),
            }
            for s in services
        ],
        "slowest": [
            {
                "service": service,
                "sha": c.sha,
                "subject": c.subject,
                "keys": keys_in(c.subject, key_re),
                "merged_at": c.merged_at.isoformat(),
                "tag": c.tag and c.tag.name,
                "tagged_at": c.tag and c.tag.at.isoformat(),
                "deployed_tag": c.deployed_tag,
                "deployed_at": c.deployed_at and c.deployed_at.isoformat(),
                "ranked_by": stage,
                "hours": round(c.hours(stage) or 0.0, 2),
            }
            for service, c, stage in slowest(measured, top)
        ],
    }


def render(data: dict) -> str:
    since, until = data["window"]["since"], data["window"]["until"]
    window = f"{since or 'start'} .. {until or 'end'}"
    lines = [f"# Release lead time ({window})", ""]
    lines.append(
        "Stages: merge -> first tag containing the change -> first "
        f"`{data['production']}` deployment of a tag containing it. "
        "p90 is nearest-rank; pending changes are counted, not timed."
    )
    lines += ["", "| Scope | Stage | Median | p90 | Changes |", "|---|---|---|---|---|"]
    rows = [("overall", data["overall"])] + [
        (s["service"], s["stages"]) for s in data["services"] if s["stages"]
    ]
    for scope, stages in rows:
        for stage in STAGES:
            median, p90, count = fmt_stat(stages[stage])
            lines.append(f"| {_md(scope)} | {STAGE_LABELS[stage]} | {median} | {p90} | {count} |")

    measured = [s["service"] for s in data["services"] if not s["problem"]]
    with_prod = data["overall_prod_services"]
    if with_prod and len(with_prod) < len(measured):
        lines += [
            "",
            f"Overall production stages cover {len(with_prod)} of {len(measured)} service(s):"
            f" {', '.join(with_prod)}.",
        ]

    for s in data["services"]:
        notes = []
        if s["problem"]:
            notes.append(f"**Not measured:** {s['problem']}")
        else:
            if s["untagged"]:
                notes.append(f"{s['untagged']} change(s) not in any release tag yet")
            if s["stages"]["tag_to_prod"]["status"] == NOT_MEASURED:
                notes.append("no production deployment recorded: production stages NOT MEASURED")
            if s["deployed_before_tagged"]:
                notes.append(
                    "deployment dated before its tag, ignored: "
                    + ", ".join(s["deployed_before_tagged"])
                )
        if notes:
            lines += ["", f"## {s['service']}", ""] + [f"- {n}" for n in notes]

    if data["slowest"]:
        lines += ["", f"## Slowest {len(data['slowest'])} change(s)", ""]
        lines += ["| Service | Change | Tag | Prod | Lead time |", "|---|---|---|---|---|"]
        for row in data["slowest"]:
            change = f"{row['subject']} ({row['sha'][:8]})"
            prod = row["deployed_tag"] or "-"
            took = f"{fmt_hours(row['hours'])} ({STAGE_LABELS[row['ranked_by']]})"
            lines.append(
                f"| {_md(row['service'])} | {_md(change)} | {row['tag'] or '-'} | {prod} | {took} |"
            )
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="release_leadtime", description="Merge -> tag -> production lead time."
    )
    p.add_argument(
        "--service",
        action="append",
        default=[],
        help="NAME or NAME=DIR, clone relative to the workspace (repeatable)",
    )
    p.add_argument("--manifest", help="version manifest naming the services (refs are ignored)")
    p.add_argument("--workspace", default=".", help="directory holding the service clones")
    p.add_argument("--mainline", default="main", help="branch the changes are merged into")
    p.add_argument(
        "--tags", action="append", default=[], help="release tag glob or name (default v*)"
    )
    p.add_argument("--deployments", help="deployment log: CSV or JSON (tag, environment, ...)")
    p.add_argument("--production", default="production", help="environment name of production")
    p.add_argument("--since", help="only changes merged at or after this ISO time")
    p.add_argument("--until", help="only changes merged before this ISO time")
    p.add_argument("--top", type=int, default=10, help="how many slowest changes to list")
    p.add_argument("--key-pattern", default=DEFAULT_KEY_PATTERN, help="regex for ticket keys")
    p.add_argument("--json", action="store_true", help="JSON instead of markdown")
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        key_re = re.compile(args.key_pattern)
        services = [parse_service(v) for v in args.service]
        if args.manifest:
            services += [
                (p.service, p.repo or p.service) for p in load_pins(args.manifest).values()
            ]
        if not services:
            raise InputError("name at least one --service or a --manifest")
        deployments = load_deployments(args.deployments) if args.deployments else None
        window = (
            parse_time(args.since, "--since") if args.since else None,
            parse_time(args.until, "--until") if args.until else None,
        )
        if args.top < 0:
            raise InputError("--top must not be negative")
    except (InputError, re.error, OSError) as exc:
        print(f"release_leadtime: {exc}", file=sys.stderr)
        return 1
    workspace = Path(args.workspace)
    globs = args.tags or ["v*"]
    results = [
        analyse_service(
            workspace, name, repo, args.mainline, globs, deployments, args.production, window
        )
        for name, repo in sorted(dict(services).items())
    ]
    data = report_data(results, args.top, key_re, window, args.production)
    text = json.dumps(data, indent=2) + "\n" if args.json else render(data)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
