"""docs/vision.md stays a short goals statement: no history, no second feature table."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VISION = ROOT / "docs" / "vision.md"
PROVENANCE = re.compile(
    r"\b(?:previously|ported|porting|elsewhere first|brought (?:over|here)|originat\w*|came from|"
    r"carried over|earlier (?:project|codebase)|another (?:codebase|repository|project))\b",
    re.I,
)


def text() -> str:
    return VISION.read_text(encoding="utf-8")


def test_under_250_words():
    assert len(re.findall(r"\S+", text())) < 250


def test_says_nothing_about_where_features_came_from():
    assert PROVENANCE.search("first proven elsewhere first and ported here")  # the check bites
    assert not PROVENANCE.search(text()), PROVENANCE.search(text()).group(0)


def test_has_no_capability_table_and_points_at_the_readme():
    assert not re.search(r"^\s*\|.*\|\s*$", text(), re.M)
    assert "README.md#3-core-capabilities" in text()
    assert "## 3. Core Capabilities" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_every_principle_has_a_home_in_the_repository():
    evidence = {
        "Quick to adopt": ROOT / "core" / "config_reader.py",
        "Additive": ROOT / "tests" / "test_ci_selection.py",
        "Stack-agnostic": ROOT / "core" / "locator_strategy.py",
        "Auditable and overridable": ROOT / "autonomous_ui" / "heal_events.py",
        "Honest reporting": ROOT / "tools" / "slash" / "healing.py",
    }
    for principle, path in evidence.items():
        assert principle in text(), principle
        assert path.exists(), path
