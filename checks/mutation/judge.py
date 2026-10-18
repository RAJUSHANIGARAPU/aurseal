"""Mutation score per package against its threshold.

score = (killed + timeout + segfault + caught by type check)
        / (that + survived + no tests)

A timeout or a crash means the tests noticed the mutant, so it counts as killed. A mutant no
test runs ("no tests") counts **against** the score: it is untested code, not neutral code.
``skipped`` and ``suspicious`` mutants are reported and left out of the fraction.

A package with zero scored mutants, or with mutants left "not checked", has no score:
``unknown``. Exactly at the threshold passes.
"""

from __future__ import annotations

from collections.abc import Mapping

from checks._outcome import CheckOutcome
from checks.mutation.mutmut import MutationCounts

KIND = "mutation"
TOP_SURVIVORS = 10


def score(counts: MutationCounts) -> float | None:
    caught = counts.killed + counts.timeout + counts.segfault + counts.caught_by_type_check
    denominator = caught + counts.survived + counts.no_tests
    return caught / denominator if denominator else None


def judge(
    counts: Mapping[str, MutationCounts | None],
    thresholds: Mapping[str, float],
    *,
    reasons: Mapping[str, str] | None = None,
) -> CheckOutcome:
    if not thresholds:
        return CheckOutcome.unknown(KIND, "no packages were judged")
    reasons = reasons or {}
    findings: list[dict] = []
    below: list[str] = []
    unanswered: list[str] = []
    metrics: dict[str, float | None] = {}
    for name, threshold in thresholds.items():
        c = counts.get(name)
        if c is None:
            unanswered.append(f"{name}: {reasons.get(name, 'no results')}")
            continue
        value = score(c)
        metrics[name] = value
        finding = {
            "package": name,
            "score": value,
            "threshold": threshold,
            "killed": c.killed,
            "survived": c.survived,
            "timeout": c.timeout,
            "no_tests": c.no_tests,
            "suspicious": c.suspicious,
            "not_checked": c.not_checked,
            "survivors": c.survivors[:TOP_SURVIVORS],
        }
        findings.append(finding)
        if c.not_checked or c.interrupted:
            unanswered.append(f"{name}: {c.not_checked + c.interrupted} mutant(s) were not checked")
        elif value is None:
            unanswered.append(f"{name}: no mutants were generated or scored")
        elif value < threshold:
            below.append(
                f"{name} {value:.2%} < {threshold:.2%} (surviving: "
                f"{', '.join(c.survivors[:TOP_SURVIVORS]) or 'none listed'})"
            )
    if below:
        extra = f"; not judged: {unanswered}" if unanswered else ""
        return CheckOutcome.breached(
            KIND, "below threshold: " + "; ".join(below) + extra, metrics=metrics, findings=findings
        )
    if unanswered:
        return CheckOutcome.unknown(KIND, "; ".join(unanswered), metrics=metrics, findings=findings)
    return CheckOutcome.ok(
        KIND,
        "every package at or above its threshold: "
        + ", ".join(f"{n} {metrics[n]:.2%}" for n in thresholds),
        metrics=metrics,
        findings=findings,
    )
