"""SQLAlchemy adapter (optional dependency): guarded, rolled back, gated writer."""

from __future__ import annotations

import pytest

from checks.db import WriteRefused, WritesNotAllowed
from checks.db.probe import SqlAlchemyProbe, open_probe

pytest.importorskip("sqlalchemy")


@pytest.fixture
def url(db_path):
    # Not the stdlib sqlite:/// form: force the adapter with the explicit driver name.
    return f"sqlite+pysqlite:///{db_path}"


def test_reads_through_sqlalchemy(url):
    probe = open_probe(url)
    assert isinstance(probe, SqlAlchemyProbe)
    rows = probe.query("SELECT id FROM orders WHERE tag = :tag", {"tag": "run-1"})
    assert rows == [{"id": 1}]
    assert probe.snapshot({"n": "SELECT COUNT(*) AS n FROM orders"}) == {"n": [{"n": 2}]}


def test_guard_refuses_writes(url):
    with pytest.raises(WriteRefused):
        SqlAlchemyProbe(url).query("DELETE FROM orders")


def test_writer_is_gated_and_writes(url):
    probe = SqlAlchemyProbe(url)
    with pytest.raises(WritesNotAllowed):
        probe.writer(allow_writes=True, env={})
    writer = probe.writer(allow_writes=True, env={"DB_WRITES_ALLOWED": "true"})
    assert writer.execute("UPDATE orders SET status = 'x' WHERE id = :id", {"id": 1}) == 1
    assert probe.query("SELECT status FROM orders WHERE id = 1") == [{"status": "x"}]
