"""A small SQLite database for the probe tests, written with sqlite3, then probed read-only."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shop.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, tag TEXT, total REAL);
        CREATE TABLE refunds (id INTEGER PRIMARY KEY, order_id INTEGER, amount REAL);
        INSERT INTO orders (status, tag, total) VALUES ('open', 'run-1', 10.0);
        INSERT INTO orders (status, tag, total) VALUES ('open', 'run-2', 20.0);
        INSERT INTO refunds (order_id, amount) VALUES (2, 5.0);
        """
    )
    conn.commit()
    conn.close()
    return path


def write(path, sql, params=()):
    """Out-of-band write, standing in for the system under test."""
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()
