"""JSON and markdown output. Counts and source names only; never a log line."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from checks._outcome import CheckOutcome
from checks.canary.canaries import RunArtifact
from checks.canary.scan import TargetResult, to_outcome

CAVEAT = (
    "CLEARED covers only the flows this run exercised. A code path the run never reached "
    "can still log personal data."
)


def to_json(results: Sequence[TargetResult], artifact: RunArtifact, backend: str) -> dict[str, Any]:
    outcome: CheckOutcome = to_outcome(results)
    return {
        "run_id": artifact.run_id,
        "window": {
            "start": artifact.started_at.isoformat(timespec="seconds"),
            "end": artifact.ended_at.isoformat(timespec="seconds") if artifact.ended_at else None,
        },
        "backend": backend,
        "canaries_seeded": len(artifact.canaries),
        "status": outcome.status,
        "reason": outcome.reason,
        "targets": [r.to_json() for r in results],
        "caveat": CAVEAT,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Canary log scan: run {report['run_id']}",
        "",
        f"Backend `{report['backend']}`, window {report['window']['start']} to "
        f"{report['window']['end'] or 'open'}, {report['canaries_seeded']} canaries seeded. "
        f"Overall: **{report['status']}** ({report['reason']}).",
        "",
        "| Target | Verdict | Hits (source: kind=count) | Reason |",
        "|---|---|---|---|",
    ]
    for t in report["targets"]:
        hits = "; ".join(
            f"{src}: " + ", ".join(f"{k}={n}" for k, n in per.items())
            for src, per in t["hits"].items()
        )
        lines.append(f"| {t['target']} | {t['verdict']} | {hits or '-'} | {t['reason']} |")
    lines += ["", report["caveat"], ""]
    return "\n".join(lines)
