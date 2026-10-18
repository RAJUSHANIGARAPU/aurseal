"""The statement guard lets plain reads through and refuses anything that can write."""

from __future__ import annotations

import pytest

from checks.db.guard import WriteRefused, read_only_problem, require_read_only


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders",
        "  select * from orders where status = 'update me'  ;",
        "WITH open AS (SELECT id FROM orders WHERE status = :s) SELECT * FROM open",
        'SELECT "delete" FROM t -- drop table t',
        "(SELECT 1)",
        "SELECT 'a;b' AS x",
    ],
)
def test_reads_are_allowed(sql):
    assert read_only_problem(sql) is None
    assert require_read_only(sql) == sql


@pytest.mark.parametrize(
    "sql,reason",
    [
        ("DELETE FROM orders", "not a SELECT"),
        ("SELECT 1; DROP TABLE orders", "more than one statement"),
        ("WITH x AS (UPDATE orders SET status = 'x' RETURNING id) SELECT * FROM x", "UPDATE"),
        ("SELECT 1 /* harmless */; /* */ DELETE FROM orders", "more than one statement"),
        ("/* SELECT */ INSERT INTO orders VALUES (1)", "not a SELECT"),
        ("SELECT * INTO backup FROM orders", "INTO"),
        ("SELECT * FROM orders FOR UPDATE", "UPDATE"),
        ("PRAGMA writable_schema = 1", "not a SELECT"),
        ("", "empty"),
        ("WITH x AS (VALUES (1)) DELETE FROM orders", "WITH without a SELECT"),
        ("WITH x AS (SELECT 1) DELETE FROM orders", "DELETE"),
    ],
)
def test_writes_are_refused(sql, reason):
    problem = read_only_problem(sql)
    assert problem and reason in problem, problem
    with pytest.raises(WriteRefused):
        require_read_only(sql)


def test_a_keyword_inside_a_literal_does_not_count():
    assert read_only_problem("SELECT id FROM orders WHERE note = 'please DELETE me'") is None
