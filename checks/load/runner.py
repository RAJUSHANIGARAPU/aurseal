"""Run a load profile against routes and record the result.

``LoadRunner`` is the seam for another load tool; ``K6Runner`` is the one built.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from checks._outcome import CheckOutcome
from checks._sink import MetricSink, metric_row
from checks.load import k6
from checks.load.profile import LoadProfile

TABLE = "metric_load_run"
DEFAULT_TIMEOUT_S = 600


class LoadRunner(Protocol):
    def run(self, routes: Sequence[str], profile: LoadProfile, out_dir: Path) -> CheckOutcome: ...


def usable_routes(raw: object) -> tuple[list[str], list[str]]:
    """Split routes into ones the tool can fetch and the rejected ones (kept for the report)."""
    if not isinstance(raw, list | tuple):
        return [], []
    usable: list[str] = []
    rejected: list[str] = []
    for entry in raw:
        candidate = entry.strip() if isinstance(entry, str) else ""
        if candidate.startswith(("http://", "https://")) and len(candidate) > len("https://"):
            usable.append(candidate)
        else:
            rejected.append(repr(entry))
    return usable, rejected


class K6Runner:
    def __init__(self, k6_bin: str | None = None, timeout_s: int = DEFAULT_TIMEOUT_S) -> None:
        self.k6_bin = k6_bin
        self.timeout_s = timeout_s

    def run(self, routes: Sequence[str], profile: LoadProfile, out_dir: Path) -> CheckOutcome:
        out_dir.mkdir(parents=True, exist_ok=True)
        script = out_dir / "load_test.js"
        summary = out_dir / "k6-summary.json"
        script.write_text(k6.render_script(routes, profile), encoding="utf-8")
        run = k6.run_k6(script, summary, self.timeout_s, self.k6_bin)
        metrics = None
        parse_error = ""
        if run.summary_path is not None:
            try:
                metrics = k6.parse_summary(run.summary_path)
            except k6.SummaryError as exc:
                parse_error = str(exc)
        outcome = k6.judge(run, metrics, profile)
        if parse_error and outcome.measured is False:
            outcome.reason = f"{outcome.reason} ({parse_error})"
        outcome.evidence = [p for p in (script, run.summary_path) if p is not None]
        if run.tool_version:
            outcome.metrics["tool_version"] = run.tool_version
        return outcome


def record(
    outcome: CheckOutcome,
    routes: Sequence[str],
    profile: LoadProfile,
    sink: MetricSink,
    nodeid: str | None = None,
) -> dict | None:
    """Write one ``metric_load_run`` row for a measured run (nothing for an unmeasured one)."""
    if not outcome.measured:
        return None
    m = outcome.metrics
    breached = (outcome.findings[0].get("breached") if outcome.findings else None) or []
    row = metric_row(
        nodeid=nodeid,
        target=" ".join(routes),
        vus_max=m.get("vus_max"),
        duration_s=profile.duration_s,
        requests=m.get("requests"),
        rps=m.get("rps"),
        p50_ms=m.get("p50_ms"),
        p95_ms=m.get("p95_ms"),
        p99_ms=m.get("p99_ms"),
        error_rate=m.get("error_rate"),
        thresholds_json=json.dumps([t.label for t in profile.thresholds]),
        breached_json=json.dumps(breached),
        tool="k6",
        tool_version=m.get("tool_version"),
    )
    sink.write(TABLE, [row])
    return row


def run_load(
    routes: Sequence[str],
    profile: LoadProfile,
    out_dir: Path,
    *,
    runner: LoadRunner | None = None,
    sink: MetricSink | None = None,
    nodeid: str | None = None,
) -> CheckOutcome:
    usable, rejected = usable_routes(list(routes))
    if not usable:
        return CheckOutcome.unknown(
            k6.KIND,
            "no usable routes were supplied: nothing was load tested",
            findings=[{"rejected_routes": rejected}],
        )
    outcome = (runner or K6Runner()).run(usable, profile, out_dir)
    if rejected:
        outcome.findings = [*outcome.findings, {"rejected_routes": rejected}]
    if sink is not None:
        record(outcome, usable, profile, sink, nodeid=nodeid)
    return outcome
