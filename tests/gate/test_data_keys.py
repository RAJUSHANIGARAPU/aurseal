"""Test-data key coverage: every key the tests read exists in every data file."""

from __future__ import annotations

import json
from pathlib import Path

from gate.data_keys import audit, merged, scan_sources

ROOT = Path(__file__).resolve().parents[2]


def test_every_key_this_repository_reads_exists():
    result = audit(
        [ROOT / "ui" / "tests", ROOT / "api" / "tests"],
        [ROOT / "ui" / "testdata" / "test_data.json"],
    )
    # Guard against the audit passing because it found nothing to check.
    assert result.refs, "no test-data reference found - did the access pattern change?"
    assert result.ok, result.describe()


SOURCE = """
def test_a(test_data, name):
    a = test_data["search"]["keyword"]
    b = test_data.get("optional", "default")
    c = test_data.get("expected")
    d = test_data[name]
    e = other["not_data"]
    test_data["written"] = 1
"""


def _setup(tmp_path: Path, data: dict, overlay: dict | None = None):
    (tmp_path / "test_x.py").write_text(SOURCE)
    (tmp_path / "base.json").write_text(json.dumps(data))
    overlays = []
    if overlay is not None:
        (tmp_path / "qa.json").write_text(json.dumps(overlay))
        overlays.append(tmp_path / "qa.json")
    return audit([tmp_path / "test_x.py"], [tmp_path / "base.json"], overlays=overlays)


def test_scan_finds_chains_and_get_without_default(tmp_path):
    (tmp_path / "test_x.py").write_text(SOURCE)
    refs = scan_sources([tmp_path], ["test_data"])
    assert [(r.path, r.resolvable) for r in refs] == [
        (("search", "keyword"), True),
        (("expected",), True),
        ((), False),
    ]
    assert str(refs[2]).endswith("test_data[<non-literal>]")


def test_missing_and_unresolvable_are_reported(tmp_path):
    result = _setup(tmp_path, {"search": {}, "expected": 1})
    assert not result.ok
    assert [ref.path for _, ref in result.missing] == [("search", "keyword")]
    assert len(result.unresolvable) == 1
    assert "missing in" in result.describe() and "unresolvable" in result.describe()


def test_every_overlay_is_checked_merged_with_its_base(tmp_path):
    data = {"search": {"keyword": "lamp"}, "expected": 1}
    result = _setup(tmp_path, data, overlay={"search": {"limit": 5}})
    assert not result.missing
    broken = _setup(tmp_path, data, overlay={"search": "flattened by the overlay"})
    assert [label for label, _ in broken.missing] == [
        f"{tmp_path / 'base.json'} + {tmp_path / 'qa.json'}"
    ]


def test_merged_is_deep():
    assert merged({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}, "d": 4}) == {
        "a": {"b": 1, "c": 3},
        "d": 4,
    }
