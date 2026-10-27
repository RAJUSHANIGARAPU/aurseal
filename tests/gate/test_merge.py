"""Merging shards: JUnit and Allure combined, completeness proven, gaps reported."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

from gate.merge import (
    check_complete,
    main,
    merge_allure,
    merge_durations,
    merge_junit,
    read_manifests,
)

JUNIT_A = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="3" time="1.5">
<testcase classname="a" name="t1"/>
<testcase classname="a" name="t2"><failure message="x"/></testcase>
<testcase classname="a" name="t3"><skipped/></testcase>
</testsuite></testsuites>"""
JUNIT_B = """<?xml version="1.0"?>
<testsuite name="pytest" tests="2" time="0.5">
<testcase classname="b" name="t4"><error message="boom"/></testcase>
<testcase classname="b" name="t5"/>
</testsuite>"""


def test_merge_junit_sums_counts(tmp_path):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    a.write_text(JUNIT_A), b.write_text(JUNIT_B)
    totals = merge_junit([a, b], tmp_path / "out" / "merged.xml")
    assert (totals.tests, totals.failures, totals.errors, totals.skipped) == (5, 1, 1, 1)
    assert totals.time == 2.0
    root = ET.parse(tmp_path / "out" / "merged.xml").getroot()
    assert root.get("tests") == "5" and len(root.findall("testsuite")) == 2


def test_complete_run_has_no_problems():
    expected = {"a", "b", "c"}
    assert check_complete(expected, {1: {"a"}, 2: {"b", "c"}}, shard_count=2) == []


def test_missing_shard_is_reported():
    problems = check_complete({"a", "b"}, {1: {"a"}}, shard_count=2)
    assert any("shard 2/2 left no results" in p for p in problems)
    assert any("ran on no shard" in p for p in problems)


def test_empty_shard_is_a_problem():
    problems = check_complete(
        {"a"}, {1: {"a"}, 2: set()}, shard_count=2, selected={1: {"a"}, 2: set()}
    )
    assert any("executed zero tests" in p for p in problems)
    assert any("selected zero tests" in p for p in problems)


def test_selected_but_never_reported_is_a_problem():
    problems = check_complete({"a", "b"}, {1: {"a", "b"}, 2: set()}, selected={1: {"a"}, 2: {"b"}})
    assert any("never reported" in p for p in problems)


def test_empty_expected_collection_cannot_prove_anything():
    assert check_complete(set(), {1: set()})


def test_merge_allure_copies_results_and_dedups_shared_files(tmp_path):
    for k, browser in ((1, "chromium"), (2, "firefox")):
        d = tmp_path / f"allure-{k}"
        d.mkdir()
        (d / f"{k}-result.json").write_text("{}")
        (d / "environment.properties").write_text(f"Browser={browser}\nBase\\ URL=http\\://x\n")
        (d / "categories.json").write_text(json.dumps([{"name": "Product"}, {"name": f"c{k}"}]))
    out = tmp_path / "merged"
    assert merge_allure([tmp_path / "allure-1", tmp_path / "allure-2", tmp_path / "nope"], out) == 2
    names = [c["name"] for c in json.loads((out / "categories.json").read_text())]
    assert names == ["Product", "c1", "c2"]
    env = (out / "environment.properties").read_text()
    assert "Browser=chromium" in env and "firefox" not in env
    assert "Base\\ URL=http\\://x" in env


def _manifests(tmp_path, shards, count, drop=()):
    collected = sorted({nid for ids in shards.values() for nid in ids})
    for k, ids in shards.items():
        (tmp_path / f"shard-{k}-of-{count}.selected.json").write_text(
            json.dumps({"shard": k, "count": count, "collected": collected, "selected": ids})
        )
        if k not in drop:
            (tmp_path / f"shard-{k}-of-{count}.executed.json").write_text(
                json.dumps({"shard": k, "count": count, "executed": ids, "exit_status": 0})
            )
        (tmp_path / f"shard-{k}-of-{count}.durations.json").write_text(
            json.dumps({nid: 0.1 for nid in ids})
        )


def test_cli_complete_and_incomplete(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    _manifests(good, {1: ["a"], 2: ["b"]}, 2)
    out = io.StringIO()
    durations = tmp_path / "durations.json"
    assert main(["--manifests", str(good), "--durations-out", str(durations)], out) == 0
    assert "complete" in out.getvalue()
    assert json.loads(durations.read_text()) == {"a": 0.1, "b": 0.1}

    bad = tmp_path / "bad"
    bad.mkdir()
    _manifests(bad, {1: ["a"], 2: ["b"]}, 2, drop=(2,))
    out = io.StringIO()
    code = main(["--manifests", str(bad), "--durations-out", str(tmp_path / "d2.json")], out)
    assert code == 1
    assert "INCOMPLETE" in out.getvalue()


def test_cli_requires_every_declared_shard(tmp_path):
    _manifests(tmp_path, {1: ["a", "b"]}, 1)
    out = io.StringIO()
    assert (
        main(
            [
                "--manifests",
                str(tmp_path),
                "--shards",
                "2",
                "--durations-out",
                str(tmp_path / "d.json"),
            ],
            out,
        )
        == 1
    )


def test_manifests_that_disagree_are_reported(tmp_path):
    _manifests(tmp_path, {1: ["a"]}, 2)
    (tmp_path / "shard-2-of-2.selected.json").write_text(
        json.dumps({"shard": 2, "count": 2, "collected": ["a", "z"], "selected": ["z"]})
    )
    manifests = read_manifests(tmp_path)
    assert any("different test sets" in p for p in manifests.problems)
    assert merge_durations(tmp_path, tmp_path / "d.json") == 1


def _serial(tmp_path, count, ids, exit_status=0, executed=None):
    collected = json.loads((tmp_path / f"shard-1-of-{count}.selected.json").read_text())[
        "collected"
    ]
    (tmp_path / f"shard-serial-of-{count}.selected.json").write_text(
        json.dumps({"shard": 0, "count": count, "collected": collected, "selected": ids})
    )
    (tmp_path / f"shard-serial-of-{count}.executed.json").write_text(
        json.dumps(
            {
                "shard": 0,
                "count": count,
                "executed": ids if executed is None else executed,
                "exit_status": exit_status,
            }
        )
    )


def _hand_to_serial(tmp_path, count, ids):
    for path in tmp_path.glob("shard-*-of-*.selected.json"):
        data = json.loads(path.read_text())
        if data["shard"] != 0:
            data["collected"] = sorted({*data["collected"], *ids})
            data["serial_selected"] = ids
            path.write_text(json.dumps(data))


def _run(tmp_path, *extra):
    out = io.StringIO()
    code = main(
        ["--manifests", str(tmp_path), "--durations-out", str(tmp_path / "d.json"), *extra], out
    )
    return code, out.getvalue()


def test_serial_lane_counts_like_a_shard(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    _hand_to_serial(tmp_path, 2, ["s"])
    _serial(tmp_path, 2, ["s"])
    code, out = _run(tmp_path, "--summary")
    assert code == 0, out
    assert "shard serial/2: selected 1, executed 1, exit 0" in out
    assert out.strip().endswith("verdict: PASS")


def test_serial_lane_that_never_ran_is_incomplete(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    _hand_to_serial(tmp_path, 2, ["s"])
    code, out = _run(tmp_path)
    assert code == 1
    assert "serial lane left no results (1 test(s) were handed to it)" in out


def test_serial_lane_that_lost_a_test_is_incomplete(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    _hand_to_serial(tmp_path, 2, ["s", "t"])
    _serial(tmp_path, 2, ["s", "t"], executed=["s"])
    code, out = _run(tmp_path)
    assert code == 1 and "shard serial: 1 selected test(s) never reported" in out


def test_failing_serial_lane_fails_the_verdict(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    _hand_to_serial(tmp_path, 2, ["s"])
    _serial(tmp_path, 2, ["s"], exit_status=1)
    code, out = _run(tmp_path, "--summary")
    assert code == 1 and "merge: complete" in out and "verdict: FAIL" in out


def test_an_empty_serial_lane_has_no_say(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    _serial(tmp_path, 2, [], exit_status=5)
    code, out = _run(tmp_path, "--summary")
    assert code == 0, out
    assert "(empty serial lane: no say)" in out
    assert "zero tests" not in out


def test_summary_verdict_fails_on_a_failed_junit_case(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    junit = tmp_path / "junit-1.xml"
    junit.write_text(JUNIT_A)
    code, out = _run(
        tmp_path, "--summary", "--junit", str(junit), "--junit-out", str(tmp_path / "m.xml")
    )
    assert code == 1 and "verdict: FAIL" in out


def test_without_summary_the_exit_code_is_completeness_only(tmp_path):
    _manifests(tmp_path, {1: ["a"], 2: ["b"]}, 2)
    _serial(tmp_path, 2, [], exit_status=0)
    (tmp_path / "shard-1-of-2.executed.json").write_text(
        json.dumps({"shard": 1, "count": 2, "executed": ["a"], "exit_status": 1})
    )
    code, out = _run(tmp_path)
    assert code == 0 and "verdict" not in out
