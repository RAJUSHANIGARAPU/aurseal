"""The documented marker inventory and ``pytest.ini`` agree, and no declared marker is dead.

``docs/whats-live.md`` ("Markers and lanes") is the full inventory: it must list exactly
the markers ``pytest.ini`` declares. The shorter marker lists elsewhere in the docs may name
only declared markers. Every declared marker must be applied by at least one test, unless
it is a lane-only marker: an opt-in lane in ``gate.lanes`` whose tests are written later
(``draft`` is applied by the generators, never by hand).

A marker applied only inside a ``pytester`` source still counts: that test is the marker's
own contract. Markers a plugin registers at runtime (``chaos``, ``known_defect``, ...) are
outside ``pytest.ini`` and outside this check.
"""

from __future__ import annotations

import configparser
import re

from gate.lanes import LANES
from tests.test_docs_drift import ROOT, _ini_markers, _table, _text

WHATS_LIVE = ROOT / "docs" / "whats-live.md"
_APPLIED = re.compile(r"\bmark\.([a-z][a-z0-9_]*)\b")


def _inventory() -> dict[str, str]:
    """``{marker: status}`` from the whats-live marker table."""
    rows = _table(_text(WHATS_LIVE), "## Markers and lanes")
    return {marker.strip("`"): status for marker, status, *_ in rows}


def _other_marker_lists() -> dict[str, set[str]]:
    """Marker names in the other marker lists of the docs, by where they are listed."""
    run_gate = _table(_text(ROOT / "docs" / "features" / "run-gate.md"), "## Lanes and markers")
    readme = _text(ROOT / "README.md").split("## 12. Markers & Segmentation", 1)[1]
    readme = readme.split("\n## ", 1)[0]
    return {
        "docs/features/run-gate.md": {re.split(r"[(`]", row[0].strip("`"))[0] for row in run_gate},
        "README.md": set(re.findall(r"^pytest -m ([a-z][a-z0-9_]*)\s*$", readme, re.M)),
    }


def _applied_markers() -> set[str]:
    """Markers some test file applies (``pytest.mark.<name>``) under the configured testpaths."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / "pytest.ini")
    applied: set[str] = set()
    for base in parser["pytest"]["testpaths"].split():
        for path in (ROOT / base).rglob("*.py"):
            applied |= set(_APPLIED.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return applied


def _dead(declared: set[str], applied: set[str], lane_only: set[str]) -> list[str]:
    return sorted(declared - applied - lane_only)


def _lane_only(inventory: dict[str, str]) -> set[str]:
    lanes = {lane.marker for lane in LANES}
    return {name for name, status in inventory.items() if status == "opt-in lane"} & lanes


def test_the_dead_marker_check_can_fail():
    assert _dead({"used", "lane", "dead"}, {"used"}, {"lane"}) == ["dead"]


def test_the_marker_table_lists_exactly_the_declared_markers():
    documented = set(_inventory())
    declared = _ini_markers()
    assert documented, "the whats-live marker table is empty"
    assert sorted(documented - declared) == [], "documented in whats-live, not in pytest.ini"
    assert sorted(declared - documented) == [], "declared in pytest.ini, missing from whats-live"


def test_other_marker_lists_name_only_declared_markers():
    declared = _ini_markers()
    for where, names in _other_marker_lists().items():
        assert names, f"no marker list found in {where}"
        assert sorted(names - declared) == [], f"{where} lists markers pytest.ini does not declare"


def test_every_declared_marker_is_applied_by_a_test_or_is_lane_only():
    dead = _dead(_ini_markers(), _applied_markers(), _lane_only(_inventory()))
    assert dead == [], f"declared markers no test applies and no lane owns: {dead}"
