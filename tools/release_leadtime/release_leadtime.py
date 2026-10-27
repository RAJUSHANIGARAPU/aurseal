"""How long a change takes from merge to production, and a release from env to env.

Two views, both over local clones, a release tag glob and deployment data.

**Changes** (unit: one first-parent commit on mainline, so a merge commit, a
squash merge or a direct commit), windowed on merge time:

* merge -> tag: to the first annotated release tag containing the change;
* merge -> prod: to the first production deployment of any tag containing it
  (when the first tag never reached production, the next deployed one did).

**Releases** (unit: one ``(service, tag)``, counted once), windowed on
production time: the time a version reached an environment is its FIRST
deployment there; a later re-pin (a rollback, a moved URL) is ignored.
Production is the earliest of the ``--production`` environments. Stages run
between consecutive environments of ``--envs`` and from the tag to production;
a stage counts only when both timestamps exist and are in order, so a hotfix
that skipped an environment drops out of those stages only. A lightweight tag
has no tag date of its own: it is listed apart and counted only in the
environment stages. Rows split into hotfix and regular by ``--hotfix-pattern``.

Every group reports n, the median, the p90 (nearest rank) and how many fall at
or under each ``--threshold``. Medians are not additive: the median of
merge -> prod is not the sum of the medians of its stages.

Deployment data comes from a log (CSV or JSON rows: service, tag, environment,
deployed_at) and/or a git repository of deploy manifests, where the committer
date of the commit that first pins a tag for an environment is the time it got
there. Without either, or with nothing recorded for a service, the stages that
need it are NOT MEASURED, never zero. Nothing is read from the clock: a rerun
over the same refs and inputs gives the same report.

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

import yaml

from tools.hotfix_mergeback.hotfix_mergeback import resolve_mainline
from tools.promotion_diff.promotion_diff import (
    DEFAULT_KEY_PATTERN,
    Git,
    InputError,
    _md,
    keys_in,
    load_pins,
    parse_pins,
)

CHANGE_STAGES = {"merge_to_tag": "merge -> tag", "merge_to_prod": "merge -> prod"}
PRODUCTION = "production"
TAG_TO_PROD = "tag -> production"
NOT_MEASURED = "NOT MEASURED"
DEFAULT_HOTFIX_PATTERN = r"-hotfix-\d+$"
DEFAULT_THRESHOLDS = (6.0, 24.0)

Window = tuple[datetime | None, datetime | None]


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


def in_window(moment: datetime | None, window: Window) -> bool:
    since, until = window
    if since is None and until is None:
        return True
    if moment is None:
        return False
    return not ((since and moment < since) or (until and moment >= until))


def split_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


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


def deploy_history(
    repo: str | Path, template: str, envs: Sequence[str], ref: str = "HEAD"
) -> tuple[list[Deployment], list[str]]:
    """First pin of every (service, tag) per environment in a repository of deploy manifests.

    ``template`` names each environment's manifest, e.g. ``envs/{env}/versions.yaml``.
    Returns the deployments and the revisions that could not be read (skipped).
    """
    git = Git(Path(repo))
    if not git.exists():
        raise InputError(f"deploy repository not found: {repo}")
    deployments, unreadable = [], []
    for env in envs:
        path = template.format(env=env)
        seen: set[tuple[str, str]] = set()
        out = git.out("log", "--reverse", "--format=%H%x1f%cI", ref, "--", path)
        for line in out.splitlines():
            sha, at = line.split("\x1f")
            shown = git.run("show", f"{sha}:{path}", check=False)
            if shown.returncode != 0:
                continue  # the file was deleted in this commit
            try:
                pins = parse_pins(shown.stdout, Path(path))
            except (InputError, ValueError, yaml.YAMLError):  # a broken revision: skip, name it
                unreadable.append(f"{sha[:8]}:{path}")
                continue
            for pin in pins.values():
                if (pin.service, pin.ref) in seen:
                    continue  # a re-pin: the first time counts
                seen.add((pin.service, pin.ref))
                deployments.append(Deployment(pin.ref, env, parse_time(at, sha), pin.service))
    return deployments, unreadable


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
    annotated: bool = True


@dataclass
class Change:
    sha: str
    subject: str
    merged_at: datetime
    tag: Tag | None = None  # first annotated tag containing it
    deployed_tag: str | None = None
    deployed_at: datetime | None = None

    def hours(self, stage: str) -> float | None:
        end = self.tag and self.tag.at if stage == "merge_to_tag" else self.deployed_at
        if end is None or end < self.merged_at:
            return None
        return (end - self.merged_at).total_seconds() / 3600


def mainline_changes(git: Git, mainline: str) -> list[Change]:
    out = git.out("log", "--first-parent", "--format=%H%x1f%cI%x1f%s", mainline)
    changes = []
    for line in out.splitlines():
        sha, at, subject = line.split("\x1f", 2)
        changes.append(Change(sha, subject, parse_time(at, sha)))
    return changes


def release_tags(git: Git, patterns: Sequence[str]) -> list[Tag]:
    """Tags matching the globs, oldest first; a lightweight tag is dated by its commit."""
    out = git.out(
        "for-each-ref",
        "--format=%(refname:short)%1f%(objecttype)%1f%(objectname)%1f%(*objectname)"
        "%1f%(creatordate:iso-strict)",
        *(f"refs/tags/{p}" for p in patterns),
    )
    tags = []
    for line in out.splitlines():
        name, kind, obj, peeled, at = line.split("\x1f")
        tags.append(Tag(name, peeled or obj, parse_time(at, name), kind == "tag"))
    return sorted(tags, key=lambda t: (t.at, t.name))


def tags_per_commit(git: Git, tags: Sequence[Tag]) -> dict[str, list[Tag]]:
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
class Release:
    """One (service, tag): when it was tagged and when it first reached each environment."""

    service: str
    tag: Tag
    hotfix: bool
    reached: dict[str, datetime] = field(default_factory=dict)  # chain env -> first arrival

    @property
    def production_at(self) -> datetime | None:
        return self.reached.get(PRODUCTION)

    def hours(self, stage: str) -> float | None:
        if stage == TAG_TO_PROD:
            if not self.tag.annotated:
                return None
            start, end = self.tag.at, self.production_at
        else:
            a, b = stage.split(" -> ")
            start, end = self.reached.get(a), self.reached.get(b)
        if start is None or end is None or end < start:
            return None
        return (end - start).total_seconds() / 3600


@dataclass
class ServiceTimes:
    service: str
    problem: str | None = None
    changes: list[Change] = field(default_factory=list)
    releases: list[Release] = field(default_factory=list)
    deploy_measured: bool = False
    deployed_before_tagged: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Settings:
    workspace: Path
    mainline: str
    tag_globs: Sequence[str]
    chain: Sequence[str]  # pre-production environments in order, then PRODUCTION
    production_envs: Sequence[str]
    hotfix_re: re.Pattern[str]
    window: Window


def first_arrivals(
    service: str, deployments: Sequence[Deployment], s: Settings
) -> dict[str, dict[str, datetime]]:
    """tag -> chain env -> first deployment; production is the earliest production env."""
    arrivals: dict[str, dict[str, datetime]] = {}
    for d in deployments:
        if d.service not in (None, service):
            continue
        if d.environment in s.production_envs:
            env = PRODUCTION
        elif d.environment in s.chain:
            env = d.environment
        else:
            continue
        per_env = arrivals.setdefault(d.tag, {})
        if env not in per_env or d.deployed_at < per_env[env]:
            per_env[env] = d.deployed_at
    return arrivals


def analyse_service(
    service: str, repo: str, deployments: Sequence[Deployment] | None, s: Settings
) -> ServiceTimes:
    times = ServiceTimes(service)
    git = Git(s.workspace / repo)
    if not git.exists():
        times.problem = f"no git clone at {s.workspace / repo}"
        return times
    main_sha = resolve_mainline(git, s.mainline)
    if main_sha is None:
        times.problem = f"mainline {s.mainline!r} not found"
        return times
    tags = release_tags(git, s.tag_globs)
    arrivals = first_arrivals(service, deployments or [], s)
    times.deploy_measured = deployments is not None and bool(arrivals)

    releases = {}
    for tag in tags:
        release = Release(
            service, tag, bool(s.hotfix_re.search(tag.name)), arrivals.get(tag.name, {})
        )
        releases[tag.name] = release
        prod = release.production_at
        if tag.annotated and prod is not None and prod < tag.at:
            times.deployed_before_tagged.add(tag.name)
        if in_window(prod, s.window):
            times.releases.append(release)

    containing = tags_per_commit(git, tags)
    for change in mainline_changes(git, main_sha):
        if not in_window(change.merged_at, s.window):
            continue
        carriers = containing.get(change.sha, [])
        change.tag = next((t for t in carriers if t.annotated), None)
        candidates = [
            (releases[t.name].production_at, t.name)
            for t in carriers
            if releases[t.name].production_at is not None
            and t.name not in times.deployed_before_tagged
        ]
        if candidates:
            change.deployed_at, change.deployed_tag = min(candidates)
        times.changes.append(change)
    return times


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest rank: the smallest value with at least ``q`` of the values at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def stats(values: Sequence[float | None], measured: bool, thresholds: Sequence[float]) -> dict:
    """``None`` values are pending: counted, not timed."""
    if not measured:
        return {"status": NOT_MEASURED}
    timed = [v for v in values if v is not None]
    pending = len(values) - len(timed)
    if not timed:
        return {"status": "no data", "n": 0, "pending": pending}
    return {
        "status": "measured",
        "n": len(timed),
        "pending": pending,
        "median_hours": round(statistics.median(timed), 2),
        "p90_hours": round(percentile(timed, 0.9), 2),
        "at_or_under": {f"{t:g}h": sum(1 for v in timed if v <= t) for t in thresholds},
    }


def release_stages(chain: Sequence[str]) -> list[str]:
    return [f"{a} -> {b}" for a, b in zip(chain, chain[1:], strict=False)] + [TAG_TO_PROD]


def change_summary(
    changes: Sequence[Change], prod_changes: Sequence[Change], prod: bool, thresholds
) -> dict:
    """``prod_changes``: the changes of the services whose deployments are measured."""
    return {
        "merge_to_tag": stats([c.hours("merge_to_tag") for c in changes], True, thresholds),
        "merge_to_prod": stats([c.hours("merge_to_prod") for c in prod_changes], prod, thresholds),
    }


def release_summary(
    releases: Sequence[Release], measured: bool, chain: Sequence[str], thresholds
) -> dict:
    return {
        stage: stats(
            [r.hours(stage) for r in releases if stage != TAG_TO_PROD or r.tag.annotated],
            measured,
            thresholds,
        )
        for stage in release_stages(chain)
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


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def report_data(
    services: Sequence[ServiceTimes],
    s: Settings,
    top: int,
    key_re: re.Pattern[str],
    thresholds: Sequence[float],
    unreadable: Sequence[str] = (),
) -> dict:
    ok = [x for x in services if not x.problem]
    deployed = [x for x in ok if x.deploy_measured]
    groups = []
    for scope, members in [("overall", deployed), *((x.service, [x]) for x in ok)]:
        measured = bool(members) and all(m.deploy_measured for m in members)
        for kind in ("regular", "hotfix"):
            rel = [r for m in members for r in m.releases if r.hotfix == (kind == "hotfix")]
            if rel or not measured:
                groups.append(
                    {
                        "scope": scope,
                        "kind": kind,
                        "units": len(rel),
                        "stages": release_summary(rel, measured, s.chain, thresholds),
                    }
                )
            if not measured:
                break  # one NOT MEASURED row per scope is enough
    return {
        "window": {"since": _iso(s.window[0]), "until": _iso(s.window[1])},
        "chain": list(s.chain),
        "production_envs": list(s.production_envs),
        "thresholds_hours": list(thresholds),
        "changes": {
            "overall": change_summary(
                [c for x in ok for c in x.changes],
                [c for x in deployed for c in x.changes],
                bool(deployed),
                thresholds,
            ),
            "overall_prod_services": [x.service for x in deployed],
            "services": [
                {
                    "service": x.service,
                    "problem": x.problem,
                    "changes": len(x.changes),
                    "untagged": sum(1 for c in x.changes if c.tag is None),
                    "stages": None
                    if x.problem
                    else change_summary(x.changes, x.changes, x.deploy_measured, thresholds),
                }
                for x in services
            ],
            "slowest": [
                {
                    "service": service,
                    "sha": c.sha,
                    "subject": c.subject,
                    "keys": keys_in(c.subject, key_re),
                    "merged_at": c.merged_at.isoformat(),
                    "tag": c.tag and c.tag.name,
                    "tagged_at": _iso(c.tag and c.tag.at),
                    "deployed_tag": c.deployed_tag,
                    "deployed_at": _iso(c.deployed_at),
                    "ranked_by": stage,
                    "hours": round(c.hours(stage) or 0.0, 2),
                }
                for service, c, stage in slowest(ok, top)
            ],
        },
        "releases": {
            "groups": groups,
            "units": [
                {
                    "service": r.service,
                    "tag": r.tag.name,
                    "kind": "hotfix" if r.hotfix else "regular",
                    "lightweight": not r.tag.annotated,
                    "tagged_at": _iso(r.tag.at) if r.tag.annotated else None,
                    "reached": {env: _iso(r.reached.get(env)) for env in s.chain},
                }
                for x in ok
                for r in x.releases
            ],
            "lightweight_tags": [
                f"{r.service} {r.tag.name}" for x in ok for r in x.releases if not r.tag.annotated
            ],
        },
        "data_errors": {
            "deployed_before_tagged": {
                x.service: sorted(x.deployed_before_tagged) for x in ok if x.deployed_before_tagged
            },
            "unreadable_deploy_revisions": list(unreadable),
        },
        "services_not_measured": {x.service: x.problem for x in services if x.problem},
    }


def fmt_hours(hours: float | None) -> str:
    if hours is None:
        return "-"
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


def fmt_stat(stat: dict, thresholds: Sequence[float]) -> list[str]:
    """``[n, median, p90, *at_or_under]``; a stage without data says why in median and p90."""
    if stat["status"] != "measured":
        status = stat["status"]
        return [str(stat.get("n", "-")), status, status, *("-" for _ in thresholds)]
    n = f"{stat['n']} (+{stat['pending']} pending)" if stat["pending"] else str(stat["n"])
    under = [str(v) for v in stat["at_or_under"].values()]
    return [n, fmt_hours(stat["median_hours"]), fmt_hours(stat["p90_hours"]), *under]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_md(c) for c in row) + " |" for row in rows]
    return lines


def render(data: dict) -> str:
    thresholds = data["thresholds_hours"]
    under = [f"<= {t:g}h" for t in thresholds]
    since, until = data["window"]["since"], data["window"]["until"]
    lines = [f"# Release lead time ({since or 'start'} .. {until or 'end'})", ""]
    lines.append(
        "p90 is nearest rank. Medians are not additive: the median of a whole path is not"
        " the sum of its stage medians. Pending units are counted, not timed."
    )

    changes = data["changes"]
    lines += ["", "## Changes (merge time in the window)", ""]
    rows = []
    for scope, stages in [("overall", changes["overall"])] + [
        (x["service"], x["stages"]) for x in changes["services"] if x["stages"]
    ]:
        for key, label in CHANGE_STAGES.items():
            rows.append([scope, label, *fmt_stat(stages[key], thresholds)])
    lines += _table(["Scope", "Stage", "n", "Median", "p90", *under], rows)
    measured = [x["service"] for x in changes["services"] if not x["problem"]]
    with_prod = changes["overall_prod_services"]
    if with_prod and len(with_prod) < len(measured):
        lines += [
            "",
            f"Overall merge -> prod covers {len(with_prod)} of {len(measured)} service(s):"
            f" {', '.join(with_prod)}.",
        ]
    untagged = [f"{x['service']} {x['untagged']}" for x in changes["services"] if x["untagged"]]
    if untagged:
        lines += ["", f"Not in any annotated release tag yet: {', '.join(untagged)}."]

    releases = data["releases"]
    chain = " -> ".join(data["chain"])
    lines += ["", f"## Releases (production time in the window; chain {chain})", ""]
    lines.append(
        f"production = earliest of {', '.join(data['production_envs'])}; one row per"
        " (service, tag); a stage counts only when both ends exist and are in order."
    )
    lines.append("")
    rows = []
    for group in releases["groups"]:
        for stage, stat in group["stages"].items():
            rows.append([group["scope"], group["kind"], stage, *fmt_stat(stat, thresholds)])
    lines += _table(["Scope", "Kind", "Stage", "n", "Median", "p90", *under], rows)
    if releases["lightweight_tags"]:
        lines += [
            "",
            "Lightweight tags (no tag date; environment stages only): "
            + ", ".join(releases["lightweight_tags"])
            + ".",
        ]

    errors = data["data_errors"]
    for service, tags in errors["deployed_before_tagged"].items():
        lines += ["", f"- {service}: production before the tag date, ignored: {', '.join(tags)}"]
    if errors["unreadable_deploy_revisions"]:
        lines += [
            "",
            "- unreadable deploy revisions: " + ", ".join(errors["unreadable_deploy_revisions"]),
        ]
    for service, problem in data["services_not_measured"].items():
        lines += ["", f"- **{service} not measured:** {problem}"]

    slow = changes["slowest"]
    if slow:
        lines += ["", f"## Slowest {len(slow)} change(s)", ""]
        rows = [
            [
                row["service"],
                f"{row['subject']} ({row['sha'][:8]})",
                row["tag"] or "-",
                row["deployed_tag"] or "-",
                f"{fmt_hours(row['hours'])} ({CHANGE_STAGES[row['ranked_by']]})",
            ]
            for row in slow
        ]
        lines += _table(["Service", "Change", "Tag", "Prod", "Lead time"], rows)
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="release_leadtime", description="Merge -> tag -> environment lead time."
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
    p.add_argument("--deploy-repo", help="git repository of deploy manifests, one per env")
    p.add_argument(
        "--deploy-file", default="{env}.yaml", help="manifest path in --deploy-repo per {env}"
    )
    p.add_argument("--deploy-ref", default="HEAD", help="history of --deploy-repo to read")
    p.add_argument("--envs", default=PRODUCTION, help="ordered environments, comma-separated")
    p.add_argument(
        "--production", default=PRODUCTION, help="production environments, comma-separated"
    )
    p.add_argument("--hotfix-pattern", default=DEFAULT_HOTFIX_PATTERN, help="regex: hotfix tags")
    p.add_argument(
        "--threshold",
        action="append",
        type=float,
        default=[],
        help="hours; report how many units fall at or under it (default 6 and 24)",
    )
    p.add_argument("--since", help="window start, ISO time (inclusive)")
    p.add_argument("--until", help="window end, ISO time (exclusive)")
    p.add_argument("--top", type=int, default=10, help="how many slowest changes to list")
    p.add_argument("--key-pattern", default=DEFAULT_KEY_PATTERN, help="regex for ticket keys")
    p.add_argument("--json", action="store_true", help="JSON instead of markdown")
    p.add_argument("-o", "--output", help="write the report here instead of stdout")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        key_re = re.compile(args.key_pattern)
        hotfix_re = re.compile(args.hotfix_pattern)
        services = [parse_service(v) for v in args.service]
        if args.manifest:
            services += [
                (p.service, p.repo or p.service) for p in load_pins(args.manifest).values()
            ]
        if not services:
            raise InputError("name at least one --service or a --manifest")
        production_envs = split_list(args.production)
        if not production_envs:
            raise InputError("--production names no environment")
        chain = [e for e in split_list(args.envs) if e not in production_envs] + [PRODUCTION]
        deployments = None
        unreadable: list[str] = []
        if args.deployments:
            deployments = load_deployments(args.deployments)
        if args.deploy_repo:
            history, unreadable = deploy_history(
                args.deploy_repo, args.deploy_file, [*chain[:-1], *production_envs], args.deploy_ref
            )
            deployments = (deployments or []) + history
        window = (
            parse_time(args.since, "--since") if args.since else None,
            parse_time(args.until, "--until") if args.until else None,
        )
        if args.top < 0:
            raise InputError("--top must not be negative")
        thresholds = sorted(args.threshold) or list(DEFAULT_THRESHOLDS)
    except (InputError, re.error, OSError, KeyError, IndexError) as exc:
        print(f"release_leadtime: {exc}", file=sys.stderr)
        return 1
    settings = Settings(
        Path(args.workspace),
        args.mainline,
        args.tags or ["v*"],
        chain,
        production_envs,
        hotfix_re,
        window,
    )
    results = [
        analyse_service(name, repo, deployments, settings)
        for name, repo in sorted(dict(services).items())
    ]
    data = report_data(results, settings, args.top, key_re, thresholds, unreadable)
    text = json.dumps(data, indent=2) + "\n" if args.json else render(data)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
