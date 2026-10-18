"""Run axe-core inside a Playwright page and read its result.

The axe-core script comes from the ``axe-playwright-python`` package (it bundles a pinned
``axe.min.js``), imported lazily. ``AXE_SCRIPT_PATH`` points at another copy of
``axe.min.js`` when the package cannot be installed. Nothing else is needed: the script is
injected with ``page.evaluate`` and ``axe.run`` is called with the policy's options.

A scan that ran no rules is not a clean scan. ``A11yReport.rules_run`` counts the rules axe
reports as evaluated (violations, passes, incomplete, inapplicable); zero means axe did not
run, and the judge reads that as ``unknown``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from checks.a11y.policy import A11yPolicy

SCRIPT_ENV = "AXE_SCRIPT_PATH"

_RUN = """async ([context, options]) => {
  const target = context === null ? document : context;
  return await axe.run(target, options);
}"""


class AxeUnavailable(RuntimeError):
    """No axe-core script could be loaded."""


def axe_source() -> str:
    explicit = os.environ.get(SCRIPT_ENV)
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise AxeUnavailable(f"{SCRIPT_ENV}={explicit} does not exist")
        return path.read_text(encoding="utf-8")
    try:
        from axe_playwright_python.base import AXE_SCRIPT
    except ImportError as exc:
        raise AxeUnavailable(
            "axe-playwright-python is not installed (pip install -r requirements-checks.txt)"
        ) from exc
    return AXE_SCRIPT


@dataclass(frozen=True)
class Violation:
    rule_id: str
    impact: str | None
    tags: tuple[str, ...]
    help: str
    nodes: int
    targets: tuple[str, ...] = ()

    @property
    def wcag_tags(self) -> tuple[str, ...]:
        return tuple(t for t in self.tags if t.startswith("wcag"))


@dataclass
class A11yReport:
    route: str
    violations: list[Violation] = field(default_factory=list)
    rules_run: int = 0
    rule_ids: frozenset[str] = frozenset()
    axe_version: str | None = None
    error: str | None = None

    def blocking(self, policy: A11yPolicy) -> list[Violation]:
        return [v for v in self.violations if policy.is_blocking(v.impact, v.rule_id)]

    @property
    def scanned(self) -> bool:
        return self.error is None and self.rules_run > 0


def _targets(nodes: list[dict[str, Any]]) -> tuple[str, ...]:
    out: list[str] = []
    for node in nodes:
        target = node.get("target") or []
        out.append(" ".join(str(t) for t in target) if isinstance(target, list) else str(target))
    return tuple(out)


def parse_results(route: str, raw: object) -> A11yReport:
    """An ``A11yReport`` from axe's result object. Anything unrecognisable is an error."""
    if not isinstance(raw, dict) or not isinstance(raw.get("violations"), list):
        return A11yReport(route=route, error="axe returned no result object")
    violations = [
        Violation(
            rule_id=str(v.get("id")),
            impact=v.get("impact"),
            tags=tuple(v.get("tags") or ()),
            help=str(v.get("help") or ""),
            nodes=len(v.get("nodes") or ()),
            targets=_targets(v.get("nodes") or []),
        )
        for v in raw["violations"]
    ]
    ids: set[str] = set()
    for key in ("violations", "passes", "incomplete", "inapplicable"):
        ids.update(str(r.get("id")) for r in raw.get(key) or () if isinstance(r, dict))
    engine = raw.get("testEngine") or {}
    return A11yReport(
        route=route,
        violations=violations,
        rules_run=len(ids),
        rule_ids=frozenset(ids),
        axe_version=engine.get("version") if isinstance(engine, dict) else None,
    )


def scan(
    page: Any,
    policy: A11yPolicy | None = None,
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    route: str | None = None,
    source: str | None = None,
) -> A11yReport:
    """Scan the page as it is now. Raises ``AxeUnavailable`` when axe cannot be loaded."""
    policy = policy or A11yPolicy()
    script = source if source is not None else axe_source()
    context: dict[str, list[list[str]]] | None = None
    if include or exclude:
        context = {}
        if include:
            context["include"] = [[s] for s in include]
        if exclude:
            context["exclude"] = [[s] for s in exclude]
    where = route or getattr(page, "url", "") or "page"
    try:
        page.evaluate(script)
        raw = page.evaluate(_RUN, [context, policy.axe_options()])
    except Exception as exc:  # noqa: BLE001 - any browser-side failure means "not scanned"
        return A11yReport(route=where, error=f"axe did not run: {type(exc).__name__}: {exc}")
    return parse_results(where, raw)
