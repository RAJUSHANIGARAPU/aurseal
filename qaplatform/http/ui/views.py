"""View models for the UI: pure functions from service documents to template context.

No web framework and no template engine here, so the rules that matter are unit-tested
offline:

* the run-trust verdict is the headline of every run, and an open run is labelled
  provisional;
* a pass count never appears without the counts that qualify it (``proved``, ``recorded``,
  ``not exercised``, ``abstained``), so a green that proved nothing cannot look like a green
  that proved everything;
* a run with nothing executed is never described as passed;
* links are rendered only for ``http(s)`` URIs; anything else (a local path, a
  ``javascript:`` string) is shown as text.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

INTEGRITY_LABEL = {"PASS": "PASS", "FAIL": "FAIL", "UNJUDGED": "UNJUDGED"}
INTEGRITY_TONE = {"PASS": "good", "FAIL": "bad", "UNJUDGED": "warn"}
OUTCOME_TONE = {
    "passed": "good",
    "xfailed": "info",
    "failed": "bad",
    "error": "bad",
    "skipped": "warn",
    "not_run": "warn",
}
EXECUTED = ("passed", "failed", "error", "xfailed")


def safe_link(uri: str | None) -> dict[str, Any]:
    """``{"text", "href"}``; ``href`` only for http(s) URIs."""
    text = uri or ""
    scheme = urlsplit(text).scheme.lower() if text else ""
    return {"text": text, "href": text if scheme in ("http", "https") else None}


def headline(summary: Mapping[str, Any]) -> dict[str, Any]:
    """What a run is, in one line that cannot overstate it."""
    integ = summary["integrity"]
    tally = summary["tally"]
    executed = sum(int(tally.get(o, 0)) for o in EXECUTED)
    verdict = integ["verdict"]
    if summary["status"] == "open":
        label = f"{INTEGRITY_LABEL[verdict]} (provisional: the run is still open)"
    else:
        label = INTEGRITY_LABEL[verdict]
    if executed == 0:
        what = "nothing executed"
    elif tally.get("failed") or tally.get("error"):
        what = f"{tally.get('failed', 0) + tally.get('error', 0)} red of {executed} executed"
    else:
        what = f"no red among {executed} executed"
    return {"verdict": verdict, "label": label, "tone": INTEGRITY_TONE[verdict], "what": what}


def qualified_counts(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Outcome counts with the proof counts beside them, always together."""
    tally = summary["tally"]
    cov = summary["verdicts"]["coverage"]
    red = summary["verdicts"]["expected_red"]
    rows = [
        {"label": "passed", "value": tally["passed"], "tone": "good"},
        {"label": "proved", "value": cov.get("proved", 0), "tone": "good"},
        {"label": "recorded only", "value": cov.get("recorded", 0), "tone": "info"},
        {"label": "not exercised", "value": cov.get("held_empty", 0), "tone": "warn"},
        {"label": "abstained", "value": cov.get("abstained", 0), "tone": "warn"},
        {"label": "failed", "value": tally["failed"], "tone": "bad"},
        {"label": "error", "value": tally["error"], "tone": "bad"},
        {"label": "xfailed", "value": tally["xfailed"], "tone": "info"},
        {"label": "skipped", "value": tally["skipped"], "tone": "warn"},
        {"label": "not run", "value": tally["not_run"], "tone": "warn"},
        {"label": "flaky (passed on retry)", "value": tally["flaky"], "tone": "warn"},
        {"label": "defects still reproducing", "value": red.get("reproduces", 0), "tone": "bad"},
        {"label": "detectors not exercised", "value": red.get("held_empty", 0), "tone": "warn"},
    ]
    selected = tally.get("selected")
    rows.insert(
        0,
        {
            "label": "received / declared",
            "value": f"{tally['received']} / {selected if selected is not None else 'undeclared'}",
            "tone": "info" if selected is not None else "warn",
        },
    )
    return rows


def run_page(
    summary: Mapping[str, Any],
    results: list[Mapping[str, Any]],
    verdicts: list[Mapping[str, Any]],
    evidence: list[Mapping[str, Any]],
) -> dict[str, Any]:
    order = {"error": 0, "failed": 1, "not_run": 2, "skipped": 3, "xfailed": 4, "passed": 5}
    rows = sorted(results, key=lambda r: (order.get(r["outcome"], 9), r["test_id"]))
    return {
        "run": summary,
        "headline": headline(summary),
        "counts": qualified_counts(summary),
        "breaches": summary["integrity"]["breaches"],
        "absent": summary["integrity"]["rules_absent"],
        "results": [
            {
                **r,
                "tone": OUTCOME_TONE.get(r["outcome"], "info"),
                "retried": r.get("attempts", 1) > 1,
            }
            for r in rows
        ],
        "verdicts": verdicts,
        "evidence": [{**e, "link": safe_link(e.get("uri"))} for e in evidence],
    }


def runs_page(runs: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in runs:
        verdict = r.get("integrity_verdict")
        if r["status"] == "open":
            label, tone = "open (not judged yet)", "info"
        else:
            label = INTEGRITY_LABEL.get(verdict or "", "unknown")
            tone = INTEGRITY_TONE.get(verdict or "", "warn")
        out.append({**r, "integrity_label": label, "tone": tone})
    return out


TRACE_TONE = {
    "proved": "good",
    "recorded_only": "info",
    "failing": "bad",
    "held_empty": "warn",
    "no_tests": "warn",
}


def requirements_page(report: Mapping[str, Any]) -> dict[str, Any]:
    rows = [{**r, "tone": TRACE_TONE[r["status"]]} for r in report["rows"]]
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["status"]] = totals.get(r["status"], 0) + 1
    return {"rows": rows, "totals": totals, "release": report.get("release")}


__all__ = [
    "headline",
    "qualified_counts",
    "requirements_page",
    "run_page",
    "runs_page",
    "safe_link",
]
