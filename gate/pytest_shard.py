"""pytest plugin: run shard K of N of the selection (``-p gate.pytest_shard --shard=K/N``).

Each process computes the same plan (:func:`gate.shard.plan_shards`) over the
items it collected and deselects everything outside shard K, so xdist workers
inside one shard agree without talking to each other. Tests marked ``e2e`` or
``stateful`` are kept together on one shard.

Each shard leaves evidence under ``--shard-dir`` (default ``reports/shards``) for
the merge step to prove completeness:

* ``shard-K-of-N.selected.json`` — the full collection and this shard's slice;
* ``shard-K-of-N.executed.json`` — node ids that actually reported, and the exit
  status;
* ``shard-K-of-N.durations.json`` — measured seconds per node id, the input of the
  next run's plan;

plus one ``metric_shard_run`` row (planned vs actual seconds, selected vs
executed). A bad ``--shard`` value is a usage error (exit 4), not an empty run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from gate.shard import (
    DURATIONS_ENV,
    ShardSpec,
    load_durations,
    parse_shard,
    plan_load,
    plan_shards,
)

PINNED_MARKERS = ("e2e", "stateful")
DEFAULT_DIR = "reports/shards"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("shard", "run one shard of the selection")
    group.addoption("--shard", default=None, help="run shard K of N, e.g. --shard=2/3")
    group.addoption(
        "--shard-durations",
        default=None,
        help=f"JSON {{nodeid: seconds}} used to balance shards (env {DURATIONS_ENV})",
    )
    group.addoption("--shard-dir", default=DEFAULT_DIR, help="where shard manifests go")


class _State:
    def __init__(self, spec: ShardSpec, out_dir: Path) -> None:
        self.spec = spec
        self.out_dir = out_dir
        self.started = time.monotonic()
        self.started_wall = time.time()
        self.planned_s: float | None = None
        self.selected: list[str] | None = None
        self.executed: set[str] = set()
        self.durations: dict[str, float] = {}

    def file(self, kind: str) -> Path:
        return self.out_dir / f"shard-{self.spec.index}-of-{self.spec.count}.{kind}.json"


def _is_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workerinput")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def pytest_configure(config: pytest.Config) -> None:
    raw = config.getoption("--shard")
    if not raw:
        return
    try:
        spec = parse_shard(raw)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    state = _State(spec, Path(config.getoption("--shard-dir")))
    config.pluginmanager.register(ShardPlugin(config, state), "gate-shard-runner")


def _durations_path(config: pytest.Config) -> str | None:
    return config.getoption("--shard-durations") or os.environ.get(DURATIONS_ENV) or None


class ShardPlugin:
    def __init__(self, config: pytest.Config, state: _State) -> None:
        self.config = config
        self.state = state

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        state = self.state
        pinned = {
            item.nodeid
            for item in items
            if any(item.get_closest_marker(name) for name in PINNED_MARKERS)
        }
        all_ids = [item.nodeid for item in items]
        durations = load_durations(_durations_path(self.config))
        plan = plan_shards(all_ids, durations, state.spec.count, pinned=pinned.__contains__)
        keep = set(plan[state.spec.index - 1])
        kept = [item for item in items if item.nodeid in keep]
        dropped = [item for item in items if item.nodeid not in keep]
        if dropped:
            self.config.hook.pytest_deselected(items=dropped)
        items[:] = kept
        state.selected = sorted(keep)
        state.planned_s = plan_load(plan, durations)[state.spec.index - 1]
        # Every process computes the same content; an atomic replace makes concurrent
        # writes from xdist workers safe.
        _write_json(
            state.file("selected"),
            {
                "shard": state.spec.index,
                "count": state.spec.count,
                "collected": sorted(all_ids),
                "selected": state.selected,
                "planned_s": state.planned_s,
            },
        )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if _is_worker(self.config):
            return  # the controller receives every worker's reports
        state = self.state
        state.executed.add(report.nodeid)
        state.durations[report.nodeid] = state.durations.get(report.nodeid, 0.0) + float(
            report.duration or 0.0
        )

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if _is_worker(self.config):
            return
        state = self.state
        selected = state.selected
        manifest = state.file("selected")
        # Under xdist the workers collected; trust their manifest only if this session wrote it.
        if (
            selected is None
            and manifest.is_file()
            and manifest.stat().st_mtime >= state.started_wall - 1
        ):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            selected = data.get("selected", [])
            state.planned_s = data.get("planned_s")
        selected = selected or []
        actual_s = round(time.monotonic() - state.started, 3)
        _write_json(
            state.file("executed"),
            {
                "shard": state.spec.index,
                "count": state.spec.count,
                "executed": sorted(state.executed),
                "exit_status": int(exitstatus),
            },
        )
        _write_json(state.file("durations"), {k: round(v, 4) for k, v in state.durations.items()})

        from checks._sink import write_metrics

        problem = write_metrics(
            "metric_shard_run",
            [
                {
                    "shard_index": state.spec.index,
                    "shard_count": state.spec.count,
                    "planned_s": state.planned_s,
                    "actual_s": actual_s,
                    "selected": len(selected),
                    "executed": len(state.executed),
                    "exit_code": int(exitstatus),
                }
            ],
        )
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                f"shard {state.spec}: selected {len(selected)}, executed {len(state.executed)}"
            )
            if problem:
                reporter.write_line(f"shard metrics: DEGRADED ({problem})")
