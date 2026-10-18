"""Judge accessibility reports against a policy and an open-issue manifest.

Known violations are not allow-listed silently. They are open issues in a
``*.manifest.json`` file (the verdict layer's format), scoped by **route** (the entry's
``template``, the URL path) and **rule** (the issue's ``finding``, the axe rule id). A
known violation is excused on its own route only, and asserted to still occur there:
when it stops, the entry reports ``KNOWN DEFECT CLEARED`` and the check fails until the
entry is removed. A new violation fails. A note (no ``finding``) excuses nothing.

The observer's capability is the set of rules axe actually evaluated on that page, so a
rule that did not run cannot be reported as "cleared".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from urllib.parse import urlparse

from checks._outcome import CheckOutcome
from checks._sink import MetricSink, metric_row
from checks.a11y.axe import A11yReport
from checks.a11y.policy import A11yPolicy
from verification.known_defects_manifest import ManifestEntry, by_template, judge_findings

KIND = "a11y"
SCAN_TABLE = "metric_a11y_scan"
VIOLATION_TABLE = "metric_a11y_violation"


def subject_for(route: str) -> str:
    """The manifest entry name of a route: its path (and query), never host or port."""
    parsed = urlparse(route)
    if not parsed.scheme:
        return route
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def judge(
    reports: Sequence[A11yReport],
    policy: A11yPolicy | None = None,
    manifest: Iterable[ManifestEntry] = (),
    *,
    record_verdicts: bool = True,
) -> CheckOutcome:
    policy = policy or A11yPolicy()
    entries = list(manifest)
    if not reports:
        return CheckOutcome.unknown(KIND, "no routes were scanned")
    findings: list[dict] = []
    problems: list[str] = []
    not_scanned: list[str] = []
    for report in reports:
        subject = subject_for(report.route)
        if not report.scanned:
            not_scanned.append(f"{subject}: {report.error or 'axe ran no rules'}")
            continue
        blocking = report.blocking(policy)
        finding = {
            "route": subject,
            "rules_run": report.rules_run,
            "blocking": [v.rule_id for v in blocking],
            "reported": [v.rule_id for v in report.violations if v not in blocking],
        }
        entry = by_template(entries, subject)
        if entry is not None:
            judged = judge_findings(
                entry,
                [v.rule_id for v in blocking],
                report.rule_ids,
                record=record_verdicts,
            )
            finding["open_issues"] = [
                {"issue": v.ticket, "verdict": v.verdict.value} for v in judged.verdicts
            ]
            unexplained = list(judged.unexplained)
            problems += [
                f"{subject}: KNOWN DEFECT CLEARED {v.ticket} ({v.detail}); remove the entry"
                for v in judged.cleared
            ]
        else:
            unexplained = [v.rule_id for v in blocking]
        if unexplained:
            detail = {v.rule_id: list(v.targets[:3]) for v in blocking if v.rule_id in unexplained}
            problems.append(f"{subject}: blocking violations {unexplained} at {detail}")
        findings.append(finding)
    if not_scanned:
        return CheckOutcome.unknown(
            KIND,
            "not every route was scanned: " + "; ".join(not_scanned),
            findings=findings + [{"not_scanned": not_scanned}],
        )
    metrics = {
        "routes": len(reports),
        "rules_run": sum(r.rules_run for r in reports),
        "violations": sum(len(r.violations) for r in reports),
    }
    if problems:
        return CheckOutcome.breached(KIND, " | ".join(problems), metrics=metrics, findings=findings)
    return CheckOutcome.ok(
        KIND,
        f"{len(reports)} route(s) scanned, no unexplained {policy.min_impact}+ violations",
        metrics=metrics,
        findings=findings,
    )


def record(
    reports: Sequence[A11yReport],
    sink: MetricSink,
    manifest: Iterable[ManifestEntry] = (),
    nodeid: str | None = None,
) -> None:
    """One scan row per scanned route (zero violations still leaves evidence) + one per rule."""
    entries = list(manifest)
    scans: list[dict] = []
    violations: list[dict] = []
    for report in reports:
        if not report.scanned:
            continue
        subject = subject_for(report.route)
        scans.append(
            metric_row(
                nodeid=nodeid,
                route=subject,
                rules_run=report.rules_run,
                violations=len(report.violations),
                axe_version=report.axe_version,
            )
        )
        entry = by_template(entries, subject)
        known = {d.finding: d.issue for d in entry.open_issues} if entry else {}
        for v in report.violations:
            violations.append(
                metric_row(
                    nodeid=nodeid,
                    route=subject,
                    rule_id=v.rule_id,
                    impact=v.impact,
                    wcag_tags=",".join(v.wcag_tags),
                    nodes=v.nodes,
                    known_defect=known.get(v.rule_id),
                )
            )
    sink.write(SCAN_TABLE, scans)
    sink.write(VIOLATION_TABLE, violations)
