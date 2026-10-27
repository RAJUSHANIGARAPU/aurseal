"""Did a retried ordered job stop at the first item that did not succeed? Before vs after.

:mod:`oracles.send_order` judges one snapshot of an ordered job against declared
orderings. A *retry* of a partly failed job has a different rule: it processes the items
in their declared order, stops at the first one that does not succeed, and leaves every
later item untouched. That rule is only visible as a delta, so this oracle takes the state
before the retry and the run(s) the retry reported, and answers one of four outcomes:

=====================  ===============================================================
PROVED                 the retry ran, every touched item lies at or before the stop,
                       and each touched item carries side-channel evidence only a real
                       execution produces (a new external transaction id)
VIOLATION              an item ranked after the first not-done item changed
NOT_EXERCISED          the shape before the retry cannot show the rule (fewer than two
                       not-done items, or no run at all)
NOT_DISTINGUISHABLE    an old build would record the same thing: more than one run in the
                       answer, an item unknown before, an item done before but not after,
                       or a change without evidence
=====================  ===============================================================

"Touched" means the status changed or a new stamp appeared. An item whose category has
no rank fails loudly (``ValueError``): a silent default rank would make the order the
oracle checks up.

:func:`report` maps the findings onto :class:`~verification.evidence_verdict.EvidenceVerdict`:
a VIOLATION fails, a PROVED is ``proved``, and anything else is ``no_verdict``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from verification.evidence_verdict import EvidenceVerdict


class Kind(str, Enum):
    PROVED = "proved"
    VIOLATION = "violation"
    NOT_EXERCISED = "not_exercised"
    NOT_DISTINGUISHABLE = "not_distinguishable"


@dataclass(frozen=True)
class ItemState:
    id: str
    category: str
    done: bool
    status: str = ""
    stamp: str | None = None
    detail: Mapping[str, Any] | None = None
    """Anything an ``evidence`` callable needs (an external transaction id, say)."""

    def __str__(self) -> str:
        return f"{self.category}/{self.id}={self.status or ('done' if self.done else 'open')}"


@dataclass(frozen=True)
class Run:
    run_id: str
    items: tuple[ItemState, ...]


@dataclass(frozen=True)
class Finding:
    kind: Kind
    item: str | None
    text: str

    def __str__(self) -> str:
        where = f" {self.item}" if self.item else ""
        return f"{self.kind.value.upper()}{where}: {self.text}"


Rank = Callable[[ItemState], "Any"]
Evidence = Callable[[ItemState, ItemState], bool]


def touched(before: ItemState, after: ItemState) -> bool:
    """The status changed, or a stamp appeared that was not there before."""
    return before.status != after.status or (
        after.stamp is not None and after.stamp != before.stamp
    )


def _rank_of(rank: Rank, item: ItemState) -> Any:
    try:
        value = rank(item)
    except (KeyError, LookupError) as exc:
        raise ValueError(f"no rank for category {item.category!r} of {item.id}") from exc
    if value is None:
        raise ValueError(f"no rank for category {item.category!r} of {item.id}")
    return value


def judge(
    before: Mapping[str, ItemState],
    after: Sequence[Run],
    rank: Rank,
    evidence: Evidence,
) -> list[Finding]:
    """Findings for one retry: one guard finding, the violations, or the proofs."""
    nd = Kind.NOT_DISTINGUISHABLE
    for item in before.values():
        _rank_of(rank, item)
    if not after:
        return [Finding(Kind.NOT_EXERCISED, None, "the answer holds no run: the retry never ran")]
    if len(after) > 1:
        runs = ", ".join(r.run_id for r in after)
        return [Finding(nd, None, f"{len(after)} runs in the answer ({runs}); expected one")]
    run = after[0]
    for item in run.items:
        _rank_of(rank, item)
        if item.id not in before:
            return [Finding(nd, item.id, "not in the before state: not the same run")]
        if before[item.id].done and not item.done:
            return [
                Finding(
                    nd,
                    item.id,
                    f"done before ({before[item.id]}) but not after ({item}): before and "
                    "after do not describe the same run",
                )
            ]
    ordered = sorted(run.items, key=lambda i: (_rank_of(rank, i), i.id))
    stop = next((i for i in ordered if not i.done), None)
    stop_key = (_rank_of(rank, stop), stop.id) if stop is not None else None
    changed = [(before[i.id], i) for i in ordered if touched(before[i.id], i)]
    violations = [
        Finding(
            Kind.VIOLATION,
            a.id,
            f"changed {b} -> {a} although it is ordered after the first not-done item {stop}",
        )
        for b, a in changed
        if stop_key is not None and (_rank_of(rank, a), a.id) > stop_key
    ]
    if violations:
        return violations
    open_before = [i for i in before.values() if not i.done]
    if len(open_before) < 2:
        return [
            Finding(
                Kind.NOT_EXERCISED,
                None,
                f"{len(open_before)} not-done item(s) before the retry; the stop rule needs two",
            )
        ]
    if not changed:
        return [Finding(nd, None, "nothing changed: a build that skipped the retry looks the same")]
    unproven = [a for b, a in changed if not evidence(b, a)]
    if unproven:
        return [
            Finding(nd, a.id, f"changed {before[a.id]} -> {a} without side-channel evidence")
            for a in unproven
        ]
    where = f"stopped at {stop}" if stop is not None else "every item done"
    return [
        Finding(Kind.PROVED, a.id, f"{before[a.id]} -> {a} with evidence; {where}")
        for _b, a in changed
    ]


def report(findings: Sequence[Finding], coverage: EvidenceVerdict) -> None:
    """VIOLATION fails, any PROVED is ``proved``, everything else is ``no_verdict``."""
    violations = [f for f in findings if f.kind is Kind.VIOLATION]
    if violations:
        raise AssertionError("; ".join(str(f) for f in violations))
    proofs = [f for f in findings if f.kind is Kind.PROVED]
    if proofs:
        coverage.proved("; ".join(str(f) for f in proofs))
        return
    coverage.no_verdict("; ".join(str(f) for f in findings) or "no finding")


__all__ = ["Finding", "ItemState", "Kind", "Run", "judge", "report", "touched"]
