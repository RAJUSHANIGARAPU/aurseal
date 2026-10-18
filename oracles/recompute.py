"""Recompute oracle anchored to an observed baseline.

"After the input was zeroed, every derived amount is zero" looks trivial to
assert and is quietly unsound: it passes both when the service recomputed and
when the response never carried those fields at all, because every lookup
returned nothing. Taking a baseline first turns that around. Only paths
observed **non-zero** before the change are compared, so every stale path is
one the service demonstrably fills and demonstrably did not update.

An empty baseline is not a verdict, it is an unmet precondition — use
:attr:`RecomputeCheck.baseline_is_empty` as the positive control and fail or
skip on it, never fold it into a pass.

A path missing afterwards counts as recomputed: dropping a field is a
legitimate way to say "no amount", and treating absence as a defect would red
the check on a response-shape change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal


def non_zero(observed: Mapping[str, Decimal | None]) -> dict[str, Decimal]:
    return {p: v for p, v in observed.items() if v is not None and v != 0}


@dataclass(frozen=True)
class RecomputeCheck:
    baseline: Mapping[str, Decimal] = field(default_factory=dict)
    after: Mapping[str, Decimal | None] = field(default_factory=dict)
    expected_after: Decimal = Decimal(0)

    @classmethod
    def of(
        cls,
        baseline: Mapping[str, Decimal | None],
        after: Mapping[str, Decimal | None],
        expected_after: Decimal = Decimal(0),
    ) -> RecomputeCheck:
        """Keeps only non-zero baseline amounts — a zero reference proves nothing."""
        return cls(non_zero(baseline), dict(after), expected_after)

    @property
    def baseline_is_empty(self) -> bool:
        return not self.baseline

    @property
    def stale_paths(self) -> list[str]:
        return [
            p
            for p in self.baseline
            if self.after.get(p) is not None and self.after[p] != self.expected_after
        ]

    @property
    def recomputed(self) -> bool:
        return not self.baseline_is_empty and not self.stale_paths

    def describe(self) -> str:
        after = {p: self.after.get(p) for p in self.baseline}
        return f"baseline(non-zero)={dict(self.baseline)} after={after} stale={self.stale_paths}"
