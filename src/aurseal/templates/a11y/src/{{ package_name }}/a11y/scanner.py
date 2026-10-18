"""Run axe-core in a Playwright page and return typed findings.

The axe build comes from ``axe-playwright-python`` (pinned through ``uv.lock``). Pass
``script`` to inject another pinned ``axe.min.js`` instead; nothing else depends on that
package, so dropping it costs one setting.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from importlib.resources import files
from typing import Any

from playwright.sync_api import Page

#: Impacts that fail a scan. Moderate and minor are reported, never blocking.
BLOCKING_IMPACTS = frozenset({"serious", "critical"})
WCAG21_AA = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa")


def bundled_axe() -> str:
    """The axe-core build that ships inside ``axe-playwright-python``."""
    return files("axe_playwright_python").joinpath("axe.min.js").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Finding:
    """One element failing one rule. ``(rule, target)`` is how the baseline knows it."""

    rule: str
    impact: str
    target: str
    help: str
    help_url: str

    @property
    def blocking(self) -> bool:
        return self.impact in BLOCKING_IMPACTS

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule, self.target)


@dataclass(frozen=True)
class ScanResult:
    url: str
    tags: tuple[str, ...]
    findings: tuple[Finding, ...]

    @classmethod
    def from_axe(cls, url: str, tags: Sequence[str], raw: dict[str, Any]) -> ScanResult:
        findings = [
            Finding(
                rule=violation["id"],
                impact=violation.get("impact") or "minor",
                # Frames and shadow roots give a selector per level; one string keys the element.
                target=" >>> ".join(str(part) for part in node["target"]),
                help=violation.get("help", ""),
                help_url=violation.get("helpUrl", ""),
            )
            for violation in raw["violations"]
            for node in violation["nodes"]
        ]
        return cls(url=url, tags=tuple(tags), findings=tuple(sorted(findings, key=_order)))

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.blocking)

    @property
    def rules(self) -> set[str]:
        return {finding.rule for finding in self.findings}

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "tags": list(self.tags),
            "findings": [asdict(finding) for finding in self.findings],
        }


def table(findings: Iterable[Finding]) -> str:
    """Findings as a Markdown table, for a failure message or a log line."""
    rows = [f"| {f.impact} | {f.rule} | `{f.target}` | {f.help} ({f.help_url}) |" for f in findings]
    if not rows:
        return "(no findings)"
    return "\n".join(["| Impact | Rule | Element | Fix |", "|---|---|---|---|", *rows])


def _order(finding: Finding) -> tuple[int, str, str]:
    severity = ("critical", "serious", "moderate", "minor")
    rank = severity.index(finding.impact) if finding.impact in severity else len(severity)
    return (rank, finding.rule, finding.target)


class Scanner:
    """Scans whatever the page shows right now: open the menu or the error state first."""

    def __init__(
        self, page: Page, *, tags: Sequence[str] = WCAG21_AA, script: str | None = None
    ) -> None:
        self.page = page
        self.tags = tuple(tags)
        self._script = script

    def scan(self, include: str | None = None) -> ScanResult:
        """Run axe on the document, or only inside the elements matching ``include``."""
        if not self.page.evaluate("() => typeof window.axe === 'object'"):
            # evaluate, not add_script_tag: a page's Content-Security-Policy cannot block it.
            self.page.evaluate(self._script or bundled_axe())
        options = {
            "runOnly": {"type": "tag", "values": list(self.tags)},
            "resultTypes": ["violations"],
        }
        raw = self.page.evaluate(
            "([include, options]) => axe.run(include ? { include: [include] } : document, options)",
            [include, options],
        )
        return ScanResult.from_axe(self.page.url, self.tags, raw)
