"""The shard plugin: K/N runs partition the selection, bad specs are usage errors."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = (
    "-p",
    "gate.pytest_shard",
    "-p",
    "no:cacheprovider",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
)

SUITE = """
import pytest

@pytest.mark.parametrize("n", range(10))
def test_unit(n):
    pass

@pytest.mark.stateful
def test_state_a():
    pass

@pytest.mark.stateful
def test_state_b():
    pass
"""


@pytest.fixture(autouse=True)
def restored_shard_environment(monkeypatch):
    """The plugin exports TEST_SHARD / TEST_WORKER_PREFIX; in-process runs must not leak them."""
    for name in ("TEST_SHARD", "TEST_WORKER_PREFIX"):
        monkeypatch.setenv(name, "")  # recorded, so teardown restores the original state
        monkeypatch.delenv(name)


def _manifest(pytester, k, n, kind):
    path = pytester.path / "shards" / f"shard-{k}-of-{n}.{kind}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def suite(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    pytester.makeini("[pytest]\nmarkers =\n    stateful\n")
    pytester.makepyfile(test_suite=SUITE)
    return pytester


def _run_shard(pytester, k, n, *extra, prefix=None):
    # Each run stands for a fresh process: nothing the previous in-process run exported.
    os.environ.pop("TEST_SHARD", None)
    os.environ.pop("TEST_WORKER_PREFIX", None)
    if prefix:
        os.environ["TEST_WORKER_PREFIX"] = prefix
    return pytester.runpytest(*PLUGIN, f"--shard={k}/{n}", "--shard-dir=shards", *extra)


def test_union_of_shards_equals_the_unsharded_run(suite):
    everything = suite.runpytest(*PLUGIN)
    everything.assert_outcomes(passed=12)
    executed = set()
    for k in (1, 2, 3):
        result = _run_shard(suite, k, 3)
        assert result.ret == 0
        ran = set(_manifest(suite, k, 3, "executed")["executed"])
        assert ran == set(_manifest(suite, k, 3, "selected")["selected"])
        assert not executed & ran, "a test ran on two shards"
        executed |= ran
    assert len(executed) == 12
    assert executed == set(_manifest(suite, 1, 3, "selected")["collected"])


def test_stateful_tests_share_a_shard(suite):
    homes = []
    for k in (1, 2, 3):
        _run_shard(suite, k, 3)
        selected = _manifest(suite, k, 3, "selected")["selected"]
        homes += [k for nid in selected if "test_state_" in nid]
    assert len(homes) == 2 and len(set(homes)) == 1


@pytest.mark.parametrize("bad", ["4/3", "0/2", "two/3"])
def test_invalid_shard_is_a_usage_error(suite, bad):
    result = suite.runpytest(*PLUGIN, f"--shard={bad}")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_no_shard_option_changes_nothing(suite):
    suite.runpytest(*PLUGIN).assert_outcomes(passed=12)
    assert not (suite.path / "shards").exists()


def test_metric_row_and_durations_written(suite, tmp_path):
    _run_shard(suite, 2, 3)
    rows = [
        json.loads(line)
        for line in (tmp_path / "metrics" / "metric_shard_run.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["shard_index"] == 2 and rows[-1]["shard_count"] == 3
    assert rows[-1]["selected"] == rows[-1]["executed"] > 0
    assert rows[-1]["exit_code"] == 0
    durations = _manifest(suite, 2, 3, "durations")
    assert set(durations) == set(_manifest(suite, 2, 3, "selected")["selected"])


def test_sharding_under_xdist_reports_every_selected_test(suite, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    result = suite.runpytest_subprocess(*PLUGIN, "--shard=1/2", "--shard-dir=shards", "-n", "2")
    assert result.ret == 0, result.stdout.str()
    selected = set(_manifest(suite, 1, 2, "selected")["selected"])
    assert selected
    assert set(_manifest(suite, 1, 2, "executed")["executed"]) == selected


SERIAL_SUITE = """
import os
import pytest

@pytest.mark.parametrize("n", range(6))
def test_unit(n):
    pass

@pytest.mark.serial
def test_serial_one():
    pass

@pytest.mark.stateful
def test_state():
    pass

def test_prefix_and_shard_are_exported():
    shard = os.environ["TEST_SHARD"]
    expected = os.environ.get("EXPECTED_PREFIX") or ("ws" if "serial" in shard else "w" + shard[0])
    assert os.environ["TEST_WORKER_PREFIX"] == expected
"""


@pytest.fixture
def serial_suite(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    monkeypatch.delenv("EXPECTED_PREFIX", raising=False)
    monkeypatch.delenv("E2E_PARALLEL_SAFE", raising=False)
    monkeypatch.delenv("SHARD_ISOLATED_MARKERS", raising=False)
    pytester.makeini("[pytest]\nmarkers =\n    stateful\n    serial\n    isolated\n")
    pytester.makepyfile(test_suite=SERIAL_SUITE)
    return pytester


def _ran(pytester, label, n):
    return set(_manifest(pytester, label, n, "executed")["executed"])


def test_serial_lane_runs_unisolated_tests_after_the_shards(serial_suite):
    parallel = set()
    for k in (1, 2):
        assert _run_shard(serial_suite, k, 2, "--serial-lane").ret == 0
        parallel |= _ran(serial_suite, k, 2)
    assert not any("serial_one" in nid or "test_state" in nid for nid in parallel)
    result = _run_shard(serial_suite, "serial", 2)
    assert result.ret == 0
    serial = _ran(serial_suite, "serial", 2)
    assert {nid.split("::")[1] for nid in serial} == {"test_serial_one", "test_state"}
    collected = set(_manifest(serial_suite, 1, 2, "selected")["collected"])
    assert parallel | serial == collected and not parallel & serial
    assert _manifest(serial_suite, 1, 2, "selected")["serial_selected"] == sorted(serial)


def test_without_the_serial_lane_nothing_moves(serial_suite):
    _run_shard(serial_suite, 1, 2)
    _run_shard(serial_suite, 2, 2)
    executed = _ran(serial_suite, 1, 2) | _ran(serial_suite, 2, 2)
    assert any("serial_one" in nid for nid in executed)
    assert _manifest(serial_suite, 1, 2, "selected")["serial_selected"] == []


def test_isolated_markers_send_every_unmarked_test_to_the_serial_lane(serial_suite):
    result = _run_shard(serial_suite, "serial", 2, "--isolated-markers=isolated")
    assert result.ret == 0
    assert len(_ran(serial_suite, "serial", 2)) == 9  # nothing carries the isolating marker


def test_an_empty_serial_lane_passes(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    pytester.makepyfile(test_suite="def test_a():\n    pass\n")
    result = _run_shard(pytester, "serial", 3)
    assert result.ret == 0, result.stdout.str()
    executed = _manifest(pytester, "serial", 3, "executed")
    assert executed["executed"] == [] and executed["exit_status"] == 0
    assert _manifest(pytester, "serial", 3, "selected")["selected"] == []


def test_each_shard_exports_its_worker_prefix(serial_suite):
    for k in (1, 2, "serial"):
        result = _run_shard(serial_suite, k, 2, "--serial-lane", "-k", "prefix_and_shard")
        # -k leaves one test, so one parallel shard selects nothing (exit 5); the empty
        # serial lane passes
        assert result.ret in ((0,) if k == "serial" else (0, 5)), result.stdout.str()
        assert result.parseoutcomes().get("failed", 0) == 0
    assert sum(len(_ran(serial_suite, k, 2)) for k in (1, 2)) == 1


def test_an_explicit_worker_prefix_is_kept(serial_suite, monkeypatch):
    monkeypatch.setenv("EXPECTED_PREFIX", "w9")
    result = _run_shard(serial_suite, 1, 1, "-k", "prefix_and_shard", prefix="w9")
    assert result.ret == 0, result.stdout.str()
    assert len(_ran(serial_suite, 1, 1)) == 1
