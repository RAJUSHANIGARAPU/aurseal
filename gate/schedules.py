"""Scheduled runs: what the workflow promises, and whether it still keeps the promise.

A nightly job has one failure mode that no test catches: it stops firing. A
scheduled workflow that is disabled, deleted, or silently paused by the CI
service produces no red build — it produces no build at all, and an absent
build reads exactly like a quiet night. So this module treats the schedule as a
claim to be measured:

* :func:`load_schedules` reads the ``schedule:`` crons a workflow declares, so a
  test can pin that the nightly exists at all.
* :func:`trigger_from_env` names what started the current run (``push``,
  ``pull_request``, ``schedule``, ``dispatch``, ``api`` or ``manual``) and
  :func:`schedule_name_from_env` names which cron fired. The results store
  records both on the run row, so "did the nightly run?" becomes a query.
* :func:`schedule_health` answers that query. The newest scheduled run older
  than ``max_age`` is **stale**; a schedule that has never fired is stale too
  (never is not healthy); no way to read history at all is **unknown**, which is
  reported as such and never as healthy.

``python -m gate.schedules --check`` prints the health of every declared
schedule. Exit 0 healthy, 1 stale, 2 unknown (no history to judge by), 3 the
workflow declares no schedule.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import yaml

from gate._env import env_float

DEFAULT_WORKFLOW = Path(".github/workflows/tests.yml")
MAX_AGE_ENV = "SCHEDULE_MAX_AGE_HOURS"
DEFAULT_MAX_AGE_HOURS = 36.0
TRIGGER_ENV = "RUN_TRIGGER"

HEALTHY = "healthy"
STALE = "stale"
UNKNOWN = "unknown"

# GitHub event names -> the run trigger vocabulary stored on a run.
_EVENT_TO_TRIGGER: Mapping[str, str] = {
    "push": "push",
    "pull_request": "pull_request",
    "pull_request_target": "pull_request",
    "schedule": "schedule",
    "workflow_dispatch": "dispatch",
    "repository_dispatch": "api",
}
TRIGGERS: frozenset[str] = frozenset({*_EVENT_TO_TRIGGER.values(), "manual"})


@dataclass(frozen=True)
class ScheduleSpec:
    """One cron entry of a workflow and the jobs that opt into scheduled runs."""

    name: str
    cron: str
    lanes: tuple[str, ...] = ()


def _on_block(workflow: Mapping) -> object:
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _mentions_schedule(expression: object) -> bool:
    return isinstance(expression, str) and "schedule" in expression


def _scheduled_lanes(workflow: Mapping) -> tuple[str, ...]:
    """Jobs that explicitly opt into scheduled runs (a job or step ``if:`` names it)."""
    lanes: list[str] = []
    for job_name, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, Mapping):
            continue
        steps = [s for s in job.get("steps") or [] if isinstance(s, Mapping)]
        if _mentions_schedule(job.get("if")) or any(_mentions_schedule(s.get("if")) for s in steps):
            lanes.append(str(job_name))
    return tuple(lanes)


def load_schedules(workflow_path: str | Path = DEFAULT_WORKFLOW) -> list[ScheduleSpec]:
    """Every ``on.schedule`` cron of a workflow; ``[]`` when it declares none."""
    data = yaml.safe_load(Path(workflow_path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        return []
    on = _on_block(data)
    if not isinstance(on, Mapping):
        return []
    entries = on.get("schedule") or []
    workflow_name = str(data.get("name") or Path(workflow_path).stem)
    lanes = _scheduled_lanes(data)
    specs: list[ScheduleSpec] = []
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("cron"):
            cron = str(entry["cron"]).strip()
            specs.append(ScheduleSpec(name=f"{workflow_name}:{cron}", cron=cron, lanes=lanes))
    return specs


def trigger_from_env(env: Mapping[str, str] | None = None) -> str:
    """What started this run. ``RUN_TRIGGER`` wins; outside CI it is ``manual``."""
    env = os.environ if env is None else env
    explicit = (env.get(TRIGGER_ENV) or "").strip().lower()
    if explicit:
        if explicit not in TRIGGERS:
            raise ValueError(f"{TRIGGER_ENV}={explicit!r} is not one of {sorted(TRIGGERS)}")
        return explicit
    event = (env.get("GITHUB_EVENT_NAME") or "").strip()
    if not event:
        return "manual"
    return _EVENT_TO_TRIGGER.get(event, "manual")


def schedule_name_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """The cron that fired this run, read from the CI event payload; None otherwise."""
    env = os.environ if env is None else env
    if trigger_from_env(env) != "schedule":
        return None
    event_path = env.get("GITHUB_EVENT_PATH")
    cron = None
    if event_path and Path(event_path).is_file():
        try:
            cron = json.loads(Path(event_path).read_text(encoding="utf-8")).get("schedule")
        except (OSError, ValueError, AttributeError):
            cron = None
    if not cron:
        return None
    workflow = env.get("GITHUB_WORKFLOW") or DEFAULT_WORKFLOW.stem
    return f"{workflow}:{cron}"


class ScheduleHistory(Protocol):
    """Reads when a schedule last produced a run. Raises when history is unreadable."""

    def last_scheduled_run(self, schedule_name: str | None = None) -> datetime | None: ...


@dataclass(frozen=True)
class ScheduleHealth:
    name: str
    status: str
    last_fired_at: datetime | None
    age: timedelta | None
    reason: str

    def describe(self) -> str:
        return f"{self.name}: {self.status.upper()} — {self.reason}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def default_max_age(env: Mapping[str, str] | None = None) -> timedelta:
    return timedelta(hours=env_float(MAX_AGE_ENV, DEFAULT_MAX_AGE_HOURS, env))


def schedule_health(
    history: ScheduleHistory | None,
    now: datetime,
    max_age: timedelta,
    schedule_name: str | None = None,
) -> ScheduleHealth:
    """Judge one schedule by its newest run. Never fired is stale; no history is unknown."""
    name = schedule_name or "schedule"
    if history is None:
        return ScheduleHealth(name, UNKNOWN, None, None, "no run history available to judge by")
    try:
        last = history.last_scheduled_run(schedule_name)
    except Exception as exc:  # unreadable history is an abstention, never a pass
        return ScheduleHealth(name, UNKNOWN, None, None, f"run history unreadable: {exc}")
    if last is None:
        return ScheduleHealth(name, STALE, None, None, "no scheduled run has ever been recorded")
    age = _aware(now) - _aware(last)
    if age > max_age:
        return ScheduleHealth(
            name, STALE, last, age, f"last scheduled run {age} ago exceeds {max_age}"
        )
    return ScheduleHealth(name, HEALTHY, last, age, f"last scheduled run {age} ago")


class StoreScheduleHistory:
    """Adapter onto the results store's named query ``last_run_by_trigger``.

    The store is any object with ``query(name, **params) -> list[dict]``; the
    query returns rows with ``started_at`` (ISO-8601) for runs whose trigger is
    ``schedule``, newest first, optionally filtered by ``schedule_name``.
    """

    QUERY = "last_run_by_trigger"

    def __init__(self, store) -> None:
        self._store = store

    def last_scheduled_run(self, schedule_name: str | None = None) -> datetime | None:
        params: dict[str, object] = {"trigger": "schedule"}
        if schedule_name:
            params["schedule_name"] = schedule_name
        rows = self._store.query(self.QUERY, **params)
        if not rows:
            return None
        return datetime.fromisoformat(str(rows[0]["started_at"]).replace("Z", "+00:00"))


def _open_store_history() -> ScheduleHistory | None:
    try:
        from results.store import open_store  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return StoreScheduleHistory(open_store(readonly=True))
    except Exception:
        return None


def main(
    argv: Sequence[str] | None = None, out=None, history: ScheduleHistory | None = None
) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m gate.schedules")
    parser.add_argument("--check", action="store_true", help="report schedule health")
    parser.add_argument("--workflow", default=str(DEFAULT_WORKFLOW))
    parser.add_argument("--max-age-hours", type=float, default=None)
    args = parser.parse_args(argv)

    specs = load_schedules(args.workflow)
    if not specs:
        print(f"no schedule declared in {args.workflow}", file=out)
        return 3
    for spec in specs:
        lanes = ", ".join(spec.lanes) or "(no job opts in)"
        print(f"{spec.name}  lanes: {lanes}", file=out)
    if not args.check:
        return 0

    max_age = (
        timedelta(hours=args.max_age_hours) if args.max_age_hours is not None else default_max_age()
    )
    history = history if history is not None else _open_store_history()
    now = datetime.now(timezone.utc)
    statuses = set()
    for spec in specs:
        health = schedule_health(history, now, max_age, spec.name)
        print(health.describe(), file=out)
        statuses.add(health.status)
    # A definite stale schedule outranks an abstention.
    if STALE in statuses:
        return 1
    return 2 if UNKNOWN in statuses else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
