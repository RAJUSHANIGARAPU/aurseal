"""Read-path helpers stay read-only (source check) and bind values."""

from __future__ import annotations

from pathlib import Path

import pytest

from checks.db import SqliteProbe
from checks.db.read_paths import count_rows, ident, rows_where, tagged_query_set
from gate.read_only import find_write_calls

READ_PATHS = Path(__file__).resolve().parents[3] / "checks" / "db" / "read_paths.py"
WRITER_CALLS = {"writer", "execute", "executescript", "executemany", "commit"}


def test_read_paths_have_no_writes():
    assert find_write_calls([READ_PATHS], forbidden=WRITER_CALLS) == []


def test_source_check_catches_a_writer(tmp_path):
    """Positive control: the same check finds a writer call when one is there."""
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text("def f(probe):\n    probe.writer(allow_writes=True)\n")
    assert [c.call for c in find_write_calls([sneaky], forbidden=WRITER_CALLS)] == ["probe.writer"]


def test_helpers(db_path):
    probe = SqliteProbe(db_path)
    assert count_rows(probe, "orders") == 2
    assert count_rows(probe, "orders", {"tag": "run-2"}) == 1
    assert rows_where(probe, "orders", ["id", "tag"], {"status": "open"}) == [
        {"id": 1, "tag": "run-1"},
        {"id": 2, "tag": "run-2"},
    ]
    qs = tagged_query_set({"orders": ["id", "status"]}, "tag")
    assert probe.snapshot(qs, {"tag": "run-2"}) == {"orders": [{"id": 2, "status": "open"}]}


@pytest.mark.parametrize("bad", ["orders; drop", "1x", "a b", ""])
def test_identifiers_are_validated(bad):
    with pytest.raises(ValueError):
        ident(bad)
