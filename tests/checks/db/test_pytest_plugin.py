"""Fixtures: no database configured means a named skip, never a pass."""

from __future__ import annotations

from pathlib import Path

pytest_plugins = ["pytester"]
ROOT = Path(__file__).resolve().parents[3]

TEST = """
from verification.invariants import InvariantSet, changed

def test_window(db_state):
    with db_state.around({"orders": "SELECT id, status FROM orders"}) as window:
        import sqlite3, os
        conn = sqlite3.connect(os.environ["SHOP_DB"])
        conn.execute("UPDATE orders SET status = 'paid' WHERE id = 1")
        conn.commit()
        conn.close()
    window.assert_invariants(InvariantSet(changed("orders")))
"""


def test_without_a_database_the_test_skips_with_a_reason(pytester, monkeypatch):
    monkeypatch.delenv("DB_URL", raising=False)
    pytester.makepyfile(test_db=TEST)
    result = pytester.runpytest("-p", "checks.db.pytest_plugin", "-rs", "-p", "no:cacheprovider")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*no database to check: set DB_URL*"])


def test_with_a_database_the_fixtures_work(pytester, monkeypatch, db_path):
    monkeypatch.setenv("DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("SHOP_DB", str(db_path))
    pytester.makepyfile(test_db=TEST)
    result = pytester.runpytest("-p", "checks.db.pytest_plugin", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
