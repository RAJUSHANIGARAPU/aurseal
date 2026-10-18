"""Query parsing: filters out, quotes kept, user text can never break MATCH."""

from __future__ import annotations

import sqlite3

import pytest

from results.search import parse_query


def test_filters_are_extracted():
    q = parse_query("timeout type:failure since:7d owner:team-a marker:smoke outcome:failed")
    assert q.terms == ("timeout",)
    assert q.filters == {
        "type": "failure",
        "since": "7d",
        "owner": "team-a",
        "marker": "smoke",
        "outcome": "failed",
    }


def test_quoted_phrases_and_unknown_keys_stay_terms():
    q = parse_query('"connection reset" http:500 checkout')
    assert q.terms == ("connection reset", "http:500", "checkout")


def test_unbalanced_quotes_do_not_raise():
    assert parse_query('"broken quote here').terms == ("broken", "quote", "here")


@pytest.mark.parametrize(
    "text",
    ['a"b', "NEAR(x y)", "x OR", "-y", "col:1", "*", "^start", "(((", "AND", 'say "hi"'],
)
def test_fts_special_characters_are_escaped(text):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("INSERT INTO t VALUES ('hello world')")
    expr = parse_query(text).match_expression()
    if expr:
        conn.execute("SELECT * FROM t WHERE t MATCH ?", (expr,)).fetchall()  # must not raise


def test_prefix_star_is_kept():
    assert parse_query("check*").match_expression() == '"check"*'
