"""Database before/after windows on the demo service's own SQLite file, opened read-only.

A private demo instance (its own database) keeps these writes away from other examples.
Snapshots are scoped by the run tag the requests carry, so they stay small.
"""

from __future__ import annotations

import pytest

from api.clients.http_engine import HttpEngine
from checks.db import DbState, SqliteProbe, WriteRefused
from checks.db.read_paths import count_rows, tagged_query_set
from demo_sut.server import DemoServer
from verification.invariants import InvariantSet, changed, non_empty, unchanged

TAG = "db-window-1"
QUERIES = {
    **tagged_query_set({"customers": ["id", "number", "status", "run_tag"]}, "run_tag"),
    "partners": "SELECT COUNT(*) AS n FROM partners",
}
VALID = {
    "type": "COMPANY",
    "display_name": "Window Probe Co",
    "billing_address": {"line1": "8 Signal Way", "postal_code": "W1 7", "city": "Fenmouth"},
}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    db = tmp_path_factory.mktemp("db-window") / "demo.sqlite"
    server = DemoServer(db_path=db).start()
    try:
        http = HttpEngine(server.base_url, max_retries=0, headers={"X-Test-Run-Id": TAG})
        yield http, SqliteProbe(db)
    finally:
        server.stop()


def test_a_create_adds_exactly_one_tagged_row_and_touches_nothing_else(demo):
    http, probe = demo
    state = DbState(probe)
    with state.around(QUERIES, {"tag": TAG}) as window:
        response = http.post("/api/customers", json=VALID)
        assert response.status == 201, response
    window.assert_invariants(
        InvariantSet(changed("customers"), non_empty("customers"), unchanged("partners"))
    )
    added = window.diff()["customers"].added
    assert len(added) == 1
    assert added[0]["number"] == response.json()["number"]
    assert added[0]["run_tag"] == TAG


def test_a_rejected_create_leaves_the_database_unchanged(demo):
    http, probe = demo
    state = DbState(probe)
    before_total = count_rows(probe, "customers")
    with state.around(QUERIES, {"tag": TAG}) as window:
        response = http.post("/api/customers", json={**VALID, "type": "ROBOT"})
        assert response.status == 422, response
    window.assert_invariants(InvariantSet(unchanged("customers"), unchanged("partners")))
    assert count_rows(probe, "customers") == before_total


def test_the_probe_cannot_write_to_the_demo_database(demo):
    _, probe = demo
    with pytest.raises(WriteRefused):
        probe.query("DELETE FROM customers")
