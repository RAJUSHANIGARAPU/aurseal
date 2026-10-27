"""Workflow actions no test names: every ``(state, action)`` pair of a workflow definition.

A pair is *named* when its action appears as a string literal (the whole literal, not a
substring) in some test module (``test_*.py`` / ``*_test.py``) under the test roots. A
name is not an assertion: this is a floor, and it only proves that nothing at all
mentions the action.

The check is a ratchet over ``traceability/workflow-gap-baseline.json``, stricter than
the coverage-gap one in both directions:

* a **new** gap (not in the baseline) fails;
* a **stale** baseline entry (no longer a gap, or no longer configured) also fails until
  it is removed, so the baseline cannot keep excusing a pair that is gone.

A config dir that is not bound (not given, missing, holding no workflow definition, or
holding a file that does not parse) is **NOT-MEASURED**: every count in the header reads
``NOT-MEASURED``, never 0, and the command exits 3, the way ``release decide`` reports a
missing verdict.

The header line, printed and written first in ``workflow-gaps.md``::

    GAPS=4 ACTIONS=17 NEW=0 STALE=0
"""

from __future__ import annotations

import ast
import json
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traceability._util import read_json, utc_now, validate

DEFAULT_BASELINE = Path("traceability") / "workflow-gap-baseline.json"
DEFAULT_TEST_ROOTS = (Path("tests"), Path("ui") / "tests", Path("api") / "tests")
CONFIG_ENV = "EXT_CONFIG_PATH"
NOT_MEASURED = "NOT-MEASURED"


class BaselineError(ValueError):
    """The baseline file exists but is not ``{"accepted": [...]}``."""


@dataclass(frozen=True)
class ActionPair:
    machine: str
    state: str
    action: str

    @property
    def ref(self) -> str:
        return f"{self.machine}:{self.state}:{self.action}"


@dataclass
class WorkflowGapReport:
    config_dir: str | None
    not_measured: str | None = None  # why nothing was measured
    pairs: list[ActionPair] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now)

    @property
    def measured(self) -> bool:
        return self.not_measured is None

    @property
    def ok(self) -> bool:
        return self.measured and not self.new and not self.stale

    def header(self) -> str:
        if not self.measured:
            return " ".join(f"{k}={NOT_MEASURED}" for k in ("GAPS", "ACTIONS", "NEW", "STALE"))
        return (
            f"GAPS={len(self.gaps)} ACTIONS={len(self.pairs)} "
            f"NEW={len(self.new)} STALE={len(self.stale)}"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "config_dir": self.config_dir,
            "measured": self.measured,
            "not_measured": self.not_measured,
            "actions": [p.ref for p in self.pairs] if self.measured else None,
            "gaps": self.gaps if self.measured else None,
            "new": self.new if self.measured else None,
            "stale": self.stale if self.measured else None,
        }

    def to_markdown(self) -> str:
        lines = [self.header(), "", "## Workflow actions no test names", ""]
        if not self.measured:
            return "\n".join(lines + [f"**Not measured:** {self.not_measured}", ""])
        if self.new:
            lines += ["**New gaps (not in the baseline):**", ""]
            lines += [f"- `{g}`" for g in self.new] + [""]
        if self.stale:
            lines += ["**Stale baseline entries (remove them):**", ""]
            lines += [f"- `{g}`" for g in self.stale] + [""]
        lines += [f"{len(self.gaps)} of {len(self.pairs)} (state, action) pair(s) unnamed:", ""]
        lines += [f"- `{g}`" for g in self.gaps]
        lines += [
            "",
            "_Named = the action is a string literal in a test module; not an assertion._",
        ]
        return "\n".join(lines) + "\n"


def action_pairs(definitions: Iterable[Any]) -> list[ActionPair]:
    """Every ``(state, action)`` a definition offers, in declaration order."""
    return [
        ActionPair(d.key, state.name, action)
        for d in definitions
        for state in d.states.values()
        for action in state.actions
    ]


def find_test_modules(roots: Sequence[str | Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                found.add(path)
    return sorted(found)


def string_literals(paths: Iterable[Path]) -> set[str]:
    """Every string constant in the modules; a module that does not parse names nothing."""
    literals: set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    return literals


def load_baseline(path: Path) -> set[str]:
    data = read_json(Path(path))
    if data is None:
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("accepted"), list):
        raise BaselineError(f"{path}: a baseline is {{'accepted': [...]}}")
    return {str(x) for x in data["accepted"]}


def resolve_config_dir(config_dir: str | Path | None) -> Path | None:
    if config_dir:
        return Path(config_dir)
    bound = os.environ.get(CONFIG_ENV, "").strip()
    return Path(bound) if bound else None


def measure(
    config_dir: str | Path | None,
    test_roots: Sequence[str | Path] = DEFAULT_TEST_ROOTS,
    baseline: Path | None = None,
) -> WorkflowGapReport:
    """Pairs, gaps and the ratchet; NOT-MEASURED when the config dir is not bound."""
    from generators.workflow_model import WorkflowError, load_dir

    directory = resolve_config_dir(config_dir)
    report = WorkflowGapReport(str(directory) if directory else None)
    if directory is None:
        report.not_measured = f"no config dir given and {CONFIG_ENV} is not set"
        return report
    if not directory.is_dir():
        report.not_measured = f"config dir not found: {directory}"
        return report
    try:
        definitions = load_dir(directory)
    except WorkflowError as exc:
        report.not_measured = f"config dir does not parse: {exc}"
        return report
    if not definitions:
        report.not_measured = f"no workflow definition in {directory}"
        return report
    report.pairs = action_pairs(definitions)
    named = string_literals(find_test_modules(test_roots))
    report.gaps = [p.ref for p in report.pairs if p.action not in named]
    if baseline is not None:
        accepted = load_baseline(baseline)
        current = set(report.gaps)
        report.new = sorted(current - accepted)
        report.stale = sorted(accepted - current)
    return report


def write_baseline(report: WorkflowGapReport, path: Path) -> Path:
    from traceability._util import write_text

    body = {
        "description": "Workflow (state, action) pairs no test module names. A new gap fails; "
        "an entry that is no longer a gap fails until it is removed.",
        "accepted": sorted(report.gaps),
    }
    return write_text(Path(path), json.dumps(body, indent=2) + "\n")


def report_json(report: WorkflowGapReport) -> dict[str, Any]:
    data = report.to_json()
    validate(data, "workflow-gap-report.schema.json")
    return data
