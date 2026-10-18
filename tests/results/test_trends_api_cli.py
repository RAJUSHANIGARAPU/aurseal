"""Trend REST handlers and CLI commands share the trends module."""

from __future__ import annotations

import json

from results.api import dispatch
from results.cli import main
from tests.results._history import add_run


def _seed(store):
    add_run(store, "r1", 1, {"a": "passed", "b": "passed"}, durations={"a": 900})
    add_run(store, "r2", 2, {"a": "failed", "b": ["failed", "passed"]}, durations={"a": 950})
    add_run(store, "r3", 3, {"a": "passed", "b": "passed"}, durations={"a": 1000})


def test_rest_handlers(store):
    _seed(store)
    hist = dispatch(store, "GET", "/cases/c-a/history")
    assert [h["outcome"] for h in hist["history"]] == ["passed", "failed", "passed"]
    trend = dispatch(store, "GET", "/cases/c-a/trend", params={"window": "10"})
    assert trend["flips"] == 2 and trend["rate_text"].startswith("67%")
    suite = dispatch(store, "GET", "/trends/suite", params={"lane": "offline"})
    assert [r["run_id"] for r in suite["runs"]] == ["r1", "r2", "r3"]
    cmp = dispatch(store, "GET", "/runs/r3/compare")
    assert cmp["baseline_run_id"] == "r2" and cmp["counts"]["fixed"] == 1
    unstable = dispatch(store, "GET", "/trends/unstable")
    assert {c["case_id"] for c in unstable["cases"]} == {"c-a", "c-b"}
    slow = dispatch(store, "GET", "/trends/slowest", params={"top": "1"})
    assert slow["cases"][0]["case_id"] == "c-a"


def test_cli(store, capsys):
    _seed(store)
    assert main(["trends", "case", "c-a"]) == 0
    assert json.loads(capsys.readouterr().out)["flips"] == 2
    assert main(["trends", "suite", "--lane", "offline"]) == 0
    assert "r3" in capsys.readouterr().out
    assert main(["compare", "r3", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["baseline_run_id"] == "r2"
    assert main(["compare", "r3"]) == 0
    assert "Run compare" in capsys.readouterr().out
    assert main(["compare", "ghost"]) == 2
    assert main(["trends", "unstable"]) == 0 and main(["trends", "slowest"]) == 0
