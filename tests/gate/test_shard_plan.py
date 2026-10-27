"""Shard planning: a deterministic, balanced partition with pinned tests kept together."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gate.shard import (
    durations_from_store,
    is_serial,
    load_durations,
    main,
    parse_shard,
    plan_load,
    plan_shards,
    shard_of,
)

ROOT = Path(__file__).resolve().parents[2]
IDS = [f"tests/test_m{i % 7}.py::test_{i}" for i in range(60)]
DURATIONS = {nid: float((i * 37) % 11 + 1) for i, nid in enumerate(IDS)}


def test_plan_is_a_partition():
    plan = plan_shards(IDS, DURATIONS, 4)
    flat = [nid for members in plan for nid in members]
    assert sorted(flat) == sorted(IDS)
    assert len(flat) == len(set(flat))


def test_plan_is_deterministic_across_calls_and_processes():
    first = plan_shards(IDS, DURATIONS, 3)
    assert plan_shards(list(reversed(IDS)), DURATIONS, 3) == first
    code = (
        "import json,sys; from gate.shard import plan_shards; "
        "ids, d = json.load(sys.stdin); print(json.dumps(plan_shards(ids, d, 3)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps([IDS, DURATIONS]),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONHASHSEED": "123", "PATH": ""},
        check=True,
    ).stdout
    assert json.loads(out) == first


def test_plan_is_balanced_within_the_lpt_bound():
    n = 4
    loads = plan_load(plan_shards(IDS, DURATIONS, n), DURATIONS)
    total, biggest = sum(DURATIONS.values()), max(DURATIONS.values())
    # Greedy LPT: no shard exceeds the ideal share by more than the largest single item.
    assert max(loads) <= total / n + biggest


def test_pinned_tests_land_on_one_shard():
    pinned = {IDS[3], IDS[17], IDS[41]}
    plan = plan_shards(IDS, DURATIONS, 3, pinned=pinned.__contains__)
    assert len({shard_of(nid, plan) for nid in pinned}) == 1


def test_unknown_durations_are_still_assigned():
    plan = plan_shards(IDS, {}, 5)
    assert sum(len(m) for m in plan) == len(IDS)
    assert all(plan)


def test_more_shards_than_tests_leaves_empty_shards():
    plan = plan_shards(IDS[:2], {}, 3)
    assert sorted(len(m) for m in plan) == [0, 1, 1]


@pytest.mark.parametrize("bad", ["4/3", "0/3", "a/b", "3", "1/0", ""])
def test_bad_shard_spec_rejected(bad):
    with pytest.raises(ValueError):
        parse_shard(bad)


def test_shard_spec_round_trip():
    assert str(parse_shard("2/3")) == "2/3"


def test_shard_of_unknown_id_raises():
    with pytest.raises(KeyError):
        shard_of("nope", [["a"]])


def test_durations_file_and_store(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"a::t": 1.5, "b::t": "x"}), encoding="utf-8")
    assert load_durations(path) == {"a::t": 1.5}
    assert load_durations(tmp_path / "missing.json") == {}

    class Store:
        def query(self, name):
            assert name == "case_durations"
            return [
                {"nodeid": "a::t", "duration_ms": 2500},
                {"nodeid": "b::t", "duration_ms": None},
            ]

    assert durations_from_store(Store()) == {"a::t": 2.5}


def test_cli_prints_plan(tmp_path):
    ids = tmp_path / "ids.txt"
    ids.write_text("\n".join(IDS[:4]) + "\n4 tests collected\n", encoding="utf-8")
    out = io.StringIO()
    assert main(["--plan", "2", "--ids", str(ids), "--durations", str(tmp_path / "x")], out) == 0
    assert out.getvalue().count("shard ") == 2


def test_durations_from_the_results_store_use_the_median_per_test():
    from gate.shard import durations_from_store
    from results.models import ResultRecord, RunRecord
    from results.store import open_store

    store = open_store(":memory:")
    for i, ms in enumerate((100, 300, 9000), start=1):
        run_id = f"r{i}"
        store.begin_run(RunRecord(run_id=run_id))
        seq = store.begin_invocation(run_id, ["tests/"], 1)
        store.add_results(
            [
                ResultRecord(
                    run_id=run_id,
                    nodeid="t.py::a",
                    outcome="passed",
                    invocation=seq,
                    duration_ms=ms,
                ),
                ResultRecord(
                    run_id=run_id, nodeid="t.py::skipped", outcome="skipped", invocation=seq
                ),
            ]
        )
    assert durations_from_store(store) == {"t.py::a": 0.3}


def test_serial_spec_parses():
    spec = parse_shard("serial/3")
    assert spec.serial and spec.count == 3 and str(spec) == "serial/3"
    assert spec.label == "serial" and not parse_shard("2/3").serial


@pytest.mark.parametrize("bad", ["serial/0", "serial", "serial/x", "Serial/2"])
def test_bad_serial_spec_rejected(bad):
    with pytest.raises(ValueError):
        parse_shard(bad)


@pytest.mark.parametrize(
    "markers, isolated, expected",
    [
        ({"serial"}, (), True),
        ({"stateful", "smoke"}, (), True),
        ({"smoke"}, (), False),
        ({"smoke"}, ("isolated",), True),
        ({"smoke", "isolated"}, ("isolated",), False),
        ({"serial", "isolated"}, ("isolated",), True),
    ],
)
def test_is_serial(markers, isolated, expected):
    assert is_serial(markers, ("serial", "stateful"), isolated) is expected
