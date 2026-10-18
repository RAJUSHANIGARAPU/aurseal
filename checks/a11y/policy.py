"""What an accessibility scan must hold: which WCAG tags run, and which impact blocks."""

from __future__ import annotations

from dataclasses import dataclass

#: axe-core impact levels, least to most severe.
IMPACTS = ("minor", "moderate", "serious", "critical")


def impact_rank(impact: str | None) -> int:
    """Rank of an impact; an unknown or missing impact ranks as the most severe.

    axe leaves ``impact`` null on some results. Treating that as harmless would let a
    violation nobody classified slip under any threshold.
    """
    try:
        return IMPACTS.index(str(impact).lower())
    except ValueError:
        return len(IMPACTS) - 1


@dataclass(frozen=True)
class A11yPolicy:
    """``min_impact``: the lowest impact that fails. ``tags``: the axe rule tags to run."""

    min_impact: str = "serious"
    tags: tuple[str, ...] = ("wcag2a", "wcag2aa")
    disabled_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.min_impact not in IMPACTS:
            raise ValueError(f"min_impact must be one of {IMPACTS}, got {self.min_impact!r}")
        if not self.tags:
            raise ValueError("a policy with no tags runs no rules")

    def is_blocking(self, impact: str | None, rule_id: str) -> bool:
        if rule_id in self.disabled_rules:
            return False
        return impact_rank(impact) >= IMPACTS.index(self.min_impact)

    def axe_options(self) -> dict:
        options: dict = {
            "runOnly": {"type": "tag", "values": list(self.tags)},
            "resultTypes": ["violations"],
        }
        if self.disabled_rules:
            options["rules"] = {r: {"enabled": False} for r in self.disabled_rules}
        return options
