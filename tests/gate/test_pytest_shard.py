"""The shard plugin: K/N runs partition the selection, bad specs are usage errors."""

from __future__ import annotations

import json
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


def _manifest(pytester, k, n, kind):
    path = pytester.path / "shards" / f"shard-{k}-of-{n}.{kind}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def suite(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    pytester.makeini("[pytest]\nmarkers =\n    stateful\n")
    pytester.makepyfile(test_suite=SUITE)
    return pytester


def _run_shard(pytester, k, n, *extra):
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
