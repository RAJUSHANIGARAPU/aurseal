"""The pull-request summary: one Markdown comment composed from a run's artefacts only.

Sections, in order: the run-trust headline (a FAIL is the first line, never a
footnote), per-lane counts, detector changes, newly flaky tests (against a ``main``
baseline, so long-known flakes do not repeat on every PR), requirement-status changes,
new coverage gaps, and generated drafts. An artefact that is missing says "not reported";
it is never shown as zero. The text is capped below GitHub's comment limit, keeping the
headline and a link to the full report.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traceability._util import read_json

MARKER = "<!-- test-run-summary -->"
MAX_CHARS = 60_000
NOT_REPORTED = "_not reported_"
_SEVERE = ("signature_mismatch", "closed_regressed", "cleared")


@dataclass
class LaneSummary:
    lane: str
    run_id: str
    verdict: str
    headline: str
    counts: dict[str, int]


@dataclass
class SummaryInputs:
    lanes: list[LaneSummary] = field(default_factory=list)
    verdicts: list[dict[str, Any]] | None = None
    baseline_verdicts: list[dict[str, Any]] | None = None
    flaky: list[str] | None = None
    baseline_flaky: list[str] | None = None
    matrix: dict[str, Any] | None = None
    baseline_matrix: dict[str, Any] | None = None
    gaps: dict[str, Any] | None = None
    drafts: dict[str, str] | None = None  # nodeid -> outcome
    run_url: str | None = None


def _markers(dirs: Sequence[Path]) -> list[dict[str, Any]]:
    out = []
    for d in dirs:
        for path in sorted(Path(d).rglob("run-trust*.json")):
            data = read_json(path)
            if isinstance(data, dict) and "verdict" in data:
                out.append(data)
    return out


def _verdicts(dirs: Sequence[Path]) -> list[dict[str, Any]] | None:
    from verification.verdicts import read_verdicts

    files = [p for d in dirs for p in sorted(Path(d).rglob("verdicts*.jsonl"))]
    if not files:
        return None
    return [r for p in files for r in read_verdicts(p)]


def _flaky(dirs: Sequence[Path]) -> list[str] | None:
    files = [p for d in dirs for p in sorted(Path(d).rglob("flakiness/report-*.json"))]
    if not files:
        return None
    data = read_json(files[-1]) or {}
    return sorted({str(t.get("test_id")) for t in data.get("flaky_tests") or ()})


def _first_json(dirs: Sequence[Path], name: str) -> dict[str, Any] | None:
    for d in dirs:
        for path in sorted(Path(d).rglob(name)):
            data = read_json(path)
            if isinstance(data, dict):
                return data
    return None


def _drafts(dirs: Sequence[Path], markers: list[dict[str, Any]]) -> dict[str, str] | None:
    from traceability.links import load_all

    drafts: set[str] = set()
    for d in dirs:
        links = load_all(d)
        drafts |= {n for n, t in links.tests.items() if t.get("review_state") == "draft"}
    if not drafts:
        return None
    outcomes: dict[str, str] = {}
    for m in markers:
        for nodeid, t in (m.get("tests") or {}).items():
            if nodeid in drafts:
                outcomes[nodeid] = str(t.get("outcome"))
    return {n: outcomes.get(n, "not_run") for n in sorted(drafts)}


def collect_inputs(reports_dirs: Sequence[Path], baseline_dir: Path | None) -> SummaryInputs:
    dirs = [Path(d) for d in reports_dirs if Path(d).exists()]
    markers = _markers(dirs)
    lanes = []
    for m in markers:
        counts: dict[str, int] = {}
        for t in (m.get("tests") or {}).values():
            counts[str(t.get("outcome"))] = counts.get(str(t.get("outcome")), 0) + 1
        lanes.append(
            LaneSummary(
                str(m.get("lane") or "default"),
                str(m.get("run_id") or ""),
                str(m.get("verdict")),
                str(m.get("headline") or ""),
                counts,
            )
        )
    base = [Path(baseline_dir)] if baseline_dir and Path(baseline_dir).exists() else []
    server, repo, run = (
        os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    return SummaryInputs(
        lanes=lanes,
        verdicts=_verdicts(dirs),
        baseline_verdicts=_verdicts(base) if base else None,
        flaky=_flaky(dirs),
        baseline_flaky=_flaky(base) if base else None,
        matrix=_first_json(dirs, "matrix.json"),
        baseline_matrix=_first_json(base, "matrix.json") if base else None,
        gaps=_first_json(dirs, "gaps.json"),
        drafts=_drafts(dirs, markers),
        run_url=f"{server}/{repo}/actions/runs/{run}" if server and repo and run else None,
    )


def _lanes(inputs: SummaryInputs) -> list[str]:
    if not inputs.lanes:
        return ["### Runs", "", f"Run-trust markers: {NOT_REPORTED}", ""]
    lines = [
        "### Runs",
        "",
        "| Lane | Integrity | Passed | Failed | Error | Skipped | Not run |",
        "|---|---|---|---|---|---|---|",
    ]
    for lane in inputs.lanes:
        c = lane.counts
        lines.append(
            f"| {lane.lane} | {lane.verdict} | {c.get('passed', 0)} | {c.get('failed', 0)} "
            f"| {c.get('error', 0)} | {c.get('skipped', 0)} | {c.get('not_run', 0)} |"
        )
    return [*lines, ""]


def _detectors(inputs: SummaryInputs) -> list[str]:
    lines = ["### Detector verdicts", ""]
    if inputs.verdicts is None:
        return [*lines, f"Verdict file: {NOT_REPORTED}", ""]
    reds = [v for v in inputs.verdicts if v.get("kind") == "expected_red"]
    severe = [v for v in reds if v.get("verdict") in _SEVERE]
    base = {
        str(v.get("ticket"))
        for v in inputs.baseline_verdicts or ()
        if v.get("verdict") == "reproduces"
    }
    new = sorted({str(v.get("ticket")) for v in reds if v.get("verdict") == "reproduces"} - base)
    for v in severe:
        lines.append(
            f"- **{v.get('verdict')}** {v.get('ticket')} in `{v.get('nodeid')}`: "
            f"{str(v.get('detail') or '')[:200]}"
        )
    if new and inputs.baseline_verdicts is not None:
        lines.append("- newly reproducing: " + ", ".join(new))
    elif new:
        lines.append("- reproducing (no baseline to compare): " + ", ".join(new))
    if len(lines) == 2:
        lines.append(f"No change ({len(reds)} detector verdict(s)).")
    return [*lines, ""]


def _flakes(inputs: SummaryInputs) -> list[str]:
    lines = ["### Newly flaky", ""]
    if inputs.flaky is None:
        return [*lines, f"Flakiness report: {NOT_REPORTED}", ""]
    if inputs.baseline_flaky is None:
        return [*lines, f"No baseline from main; {len(inputs.flaky)} flaky test(s) in total.", ""]
    new = sorted(set(inputs.flaky) - set(inputs.baseline_flaky))
    lines += [f"- `{t}`" for t in new] or ["None."]
    return [*lines, ""]


def _requirements(inputs: SummaryInputs) -> list[str]:
    lines = ["### Requirements", ""]
    if inputs.matrix is None:
        return [*lines, f"Traceability matrix: {NOT_REPORTED}", ""]
    lines.append(f"**{inputs.matrix.get('headline')}**")
    if inputs.baseline_matrix is not None:
        before = {r["requirement_id"]: r["status"] for r in inputs.baseline_matrix.get("rows", [])}
        changed = [
            f"- {r['requirement_id']}: {before.get(r['requirement_id'], 'new')} → {r['status']}"
            for r in inputs.matrix.get("rows", [])
            if before.get(r["requirement_id"]) != r["status"]
        ]
        lines += ["", *(changed or ["No status changed versus main."])]
    return [*lines, ""]


def _gaps(inputs: SummaryInputs) -> list[str]:
    lines = ["### Coverage gaps", ""]
    if inputs.gaps is None:
        return [*lines, f"Gap report: {NOT_REPORTED}", ""]
    new = inputs.gaps.get("new_gaps") or []
    lines += [f"- new gap `{g}`" for g in new] or ["No new gap."]
    return [*lines, ""]


def _drafts_section(inputs: SummaryInputs) -> list[str]:
    if inputs.drafts is None:
        return []
    lines = ["### Generated tests awaiting review", ""]
    lines += [f"- `{n}`: {o}" for n, o in inputs.drafts.items()]
    return [*lines, ""]


def headline(inputs: SummaryInputs) -> str:
    failed = [lane.lane for lane in inputs.lanes if lane.verdict == "FAIL"]
    if failed:
        return f"## RUN TRUST FAILED ({_few(failed)}): THIS RUN IS NOT EVIDENCE"
    if not inputs.lanes:
        return "## Test run: no run-trust marker was reported"
    return "## Test run: run trust " + _few([f"{x.lane} {x.verdict}" for x in inputs.lanes])


def _few(items: list[str], limit: int = 6) -> str:
    """At most ``limit`` names, so the headline always fits and survives truncation."""
    shown = ", ".join(items[:limit])
    return shown if len(items) <= limit else f"{shown} and {len(items) - limit} more"


def render_pr_summary(inputs: SummaryInputs, max_chars: int = MAX_CHARS) -> str:
    head = [MARKER, headline(inputs), ""]
    body = [
        *_lanes(inputs),
        *_detectors(inputs),
        *_flakes(inputs),
        *_requirements(inputs),
        *_gaps(inputs),
        *_drafts_section(inputs),
    ]
    if inputs.run_url:
        body.append(f"Full reports: {inputs.run_url}")
    text = "\n".join(head + body) + "\n"
    if len(text) <= max_chars:
        return text
    top = "\n".join(head) + "\n"
    tail = (
        "\n\n_Truncated; the full summary is in the run's artefacts"
        f"{': ' + inputs.run_url if inputs.run_url else ''}._\n"
    )
    room = max(max_chars - len(top) - len(tail), 0)
    rest = "\n".join(body)[:room].rsplit("\n", 1)[0]
    return top + rest + tail


def pr_number(event_path: str | None) -> tuple[int | None, bool]:
    """``(PR number, is_fork)`` from the event payload; ``(None, False)`` outside a PR."""
    data = read_json(Path(event_path)) if event_path else None
    pr = (data or {}).get("pull_request") if isinstance(data, dict) else None
    if not isinstance(pr, dict):
        return None, False
    head = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    base = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
    return int(pr["number"]), bool(head and base and head != base)


__all__ = ["MARKER", "SummaryInputs", "collect_inputs", "pr_number", "render_pr_summary"]
