"""k6: render a script from routes and a profile, run it, parse the summary, judge it.

Verified against k6 v2.3.0: ``k6 run --summary-export <file>`` writes the legacy
summary (``metrics.<name>.thresholds`` maps each expression to ``true`` when it was
*crossed*), and the process exits ``99`` when any threshold was crossed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from checks._outcome import CheckOutcome
from checks.load.profile import LoadProfile

KIND = "load"
K6_ENV = "K6_BIN"
THRESHOLDS_CROSSED = 99
TREND_STATS = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"]

_SCRIPT = """import http from 'k6/http';
import {{ check, sleep }} from 'k6';

export const options = {options};

const ROUTES = {routes};
const THINK_TIME_S = {think};

export default function () {{
  for (const url of ROUTES) {{
    const res = http.get(url);
    check(res, {{ 'status is 2xx': (r) => r.status >= 200 && r.status < 300 }});
  }}
  if (THINK_TIME_S > 0) {{
    sleep(THINK_TIME_S);
  }}
}}
"""


def render_script(routes: Sequence[str], profile: LoadProfile) -> str:
    """The k6 script. Every value is JSON-encoded, so no route can break out of its string."""
    options = {
        "stages": [{"duration": d, "target": t} for d, t in profile.stages],
        "thresholds": profile.k6_thresholds(),
        "summaryTrendStats": TREND_STATS,
    }
    return _SCRIPT.format(
        options=json.dumps(options, indent=2),
        routes=json.dumps(list(routes)),
        think=json.dumps(profile.think_time_s),
    )


@dataclass
class K6Run:
    ran: bool
    exit_code: int | None = None
    timed_out: bool = False
    reason: str = ""
    output_tail: str = ""
    summary_path: Path | None = None
    tool_version: str | None = None


@dataclass
class LoadMetrics:
    requests: int
    rps: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    error_rate: float | None
    vus_max: int | None
    checks_rate: float | None
    thresholds: dict[str, bool] = field(default_factory=dict)

    @property
    def breached(self) -> list[str]:
        return sorted(k for k, crossed in self.thresholds.items() if crossed)


class SummaryError(ValueError):
    """The summary file is missing or is not a k6 summary."""


def k6_binary(explicit: str | None = None) -> str | None:
    candidate = explicit or os.environ.get(K6_ENV)
    if candidate:
        return candidate if Path(candidate).is_file() else shutil.which(candidate)
    return shutil.which("k6")


def tool_version(binary: str) -> str | None:
    try:
        proc = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = (proc.stdout or "").split()
    return words[1] if len(words) > 1 and words[0] == "k6" else None


def run_k6(script: Path, summary: Path, timeout_s: int, k6_bin: str | None = None) -> K6Run:
    binary = k6_binary(k6_bin)
    if binary is None:
        return K6Run(ran=False, reason="k6 is not installed: no load was applied")
    summary.unlink(missing_ok=True)
    cmd = [binary, "run", "--quiet", "--no-color", "--summary-export", str(summary), str(script)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return K6Run(ran=True, timed_out=True, reason=f"k6 did not finish within {timeout_s}s")
    except OSError as exc:
        return K6Run(ran=False, reason=f"k6 could not be started: {type(exc).__name__}: {exc}")
    return K6Run(
        ran=True,
        exit_code=proc.returncode,
        output_tail=((proc.stdout or "") + (proc.stderr or ""))[-800:],
        summary_path=summary if summary.is_file() else None,
        tool_version=tool_version(binary),
    )


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse_summary(path: Path) -> LoadMetrics:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SummaryError(f"cannot read k6 summary {path}: {exc}") from exc
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(metrics, dict) or "http_reqs" not in metrics:
        raise SummaryError(f"{path} is not a k6 summary export (no metrics.http_reqs)")
    reqs = metrics.get("http_reqs") or {}
    duration = metrics.get("http_req_duration") or {}
    failed = metrics.get("http_req_failed") or {}
    checks = metrics.get("checks") or {}
    vus_max = metrics.get("vus_max") or {}
    thresholds: dict[str, bool] = {}
    for name, body in metrics.items():
        for expr, crossed in ((body or {}).get("thresholds") or {}).items():
            thresholds[f"{name}: {expr}"] = bool(crossed)
    count = _num(reqs.get("count"))
    if count is None:
        raise SummaryError(f"{path}: http_reqs has no count")
    vmax = _num(vus_max.get("max", vus_max.get("value")))
    return LoadMetrics(
        requests=int(count),
        rps=_num(reqs.get("rate")) or 0.0,
        p50_ms=_num(duration.get("med")),
        p95_ms=_num(duration.get("p(95)")),
        p99_ms=_num(duration.get("p(99)")),
        error_rate=_num(failed.get("value")),
        vus_max=int(vmax) if vmax is not None else None,
        checks_rate=_num(checks.get("value")),
        thresholds=thresholds,
    )


def judge(run: K6Run, metrics: LoadMetrics | None, profile: LoadProfile) -> CheckOutcome:
    """The verdict for one k6 run. Only a measured run with thresholds can pass or fail."""
    expected = [t.label for t in profile.thresholds]
    if not run.ran:
        return CheckOutcome.unknown(KIND, run.reason)
    if run.timed_out:
        return CheckOutcome.unknown(KIND, run.reason)
    measured = _metric_dict(metrics) if metrics else {}
    if run.exit_code == THRESHOLDS_CROSSED:
        breached = metrics.breached if metrics else []
        named = ", ".join(breached) if breached else "not named: the summary was unreadable"
        return CheckOutcome.breached(
            KIND,
            f"k6 reported crossed thresholds: {named}",
            metrics=measured,
            findings=[{"breached": breached, "thresholds": expected}],
        )
    if run.exit_code != 0:
        return CheckOutcome.unknown(
            KIND,
            f"k6 exited {run.exit_code}, which is not a threshold verdict (script or "
            f"config problem): {run.output_tail[-300:]}",
            metrics=measured,
        )
    if metrics is None:
        return CheckOutcome.unknown(KIND, "k6 exited 0 but left no readable summary")
    if metrics.requests == 0:
        return CheckOutcome.unknown(KIND, "k6 made no requests: nothing was measured")
    missing = [label for label in expected if label not in metrics.thresholds]
    if missing:
        return CheckOutcome.unknown(
            KIND, f"the summary does not report thresholds {missing}: they were not evaluated"
        )
    if metrics.breached:
        return CheckOutcome.breached(
            KIND,
            f"thresholds crossed: {', '.join(metrics.breached)}",
            metrics=measured,
            findings=[{"breached": metrics.breached, "thresholds": expected}],
        )
    return CheckOutcome.ok(
        KIND,
        f"{metrics.requests} requests, every threshold held",
        metrics=measured,
        findings=[{"breached": [], "thresholds": expected}],
    )


def _metric_dict(metrics: LoadMetrics) -> dict[str, object]:
    return {
        "requests": metrics.requests,
        "rps": metrics.rps,
        "p50_ms": metrics.p50_ms,
        "p95_ms": metrics.p95_ms,
        "p99_ms": metrics.p99_ms,
        "error_rate": metrics.error_rate,
        "vus_max": metrics.vus_max,
        "checks_rate": metrics.checks_rate,
    }
