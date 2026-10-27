"""A sharded run is judged once: shard markers merged, held to the full floor."""

from __future__ import annotations

from gate.integrity import (
    Outcome,
    RunTrustGate,
    TestResult,
    Thresholds,
    build_tally,
    main,
    merge_shard_markers,
    read_marker,
    write_markers,
)

RUN = "run-77-1"


def _shard(directory, tests, *, run_id=RUN, min_executed=2):
    gate = RunTrustGate(Thresholds(min_executed=min_executed))
    tally = build_tally(tests)
    write_markers(
        directory,
        run_id=run_id,
        lane="browser",
        tests=tests,
        tally=tally,
        result=gate.evaluate(tally),
        gate=gate,
    )


def _passed(n: int, prefix: str) -> list[TestResult]:
    return [TestResult(f"t::{prefix}{i}", Outcome.PASSED) for i in range(n)]


def test_each_shard_passes_its_floor_but_the_whole_run_does_not(tmp_path):
    one, two, out = tmp_path / "s1", tmp_path / "s2", tmp_path / "merged"
    for directory, prefix in ((one, "a"), (two, "b")):
        skipped = [TestResult(f"t::{prefix}s{i}", Outcome.SKIPPED, (), "down") for i in range(3)]
        _shard(directory, _passed(3, prefix) + skipped, min_executed=3)  # a shard's own lower floor
    result, problems = merge_shard_markers(
        [one, two], run_id=RUN, lane="browser", out_dir=out, thresholds=Thresholds(min_executed=10)
    )
    assert problems == [] and result is not None and result.breaches
    assert result.breaches[0].rule == "EXECUTED FLOOR"
    merged = read_marker(out, "browser")
    assert merged["selected"] == 12 and merged["run_id"] == RUN


def test_a_whole_run_at_the_floor_passes(tmp_path):
    one, two = tmp_path / "s1", tmp_path / "s2"
    _shard(one, _passed(5, "a"))
    _shard(two, _passed(5, "b"))
    result, _ = merge_shard_markers(
        [one, two],
        run_id=RUN,
        lane="browser",
        out_dir=tmp_path / "m",
        thresholds=Thresholds(min_executed=10),
    )
    assert result is not None and not result.breaches


def test_a_missing_or_foreign_marker_is_a_problem_not_a_smaller_run(tmp_path):
    one, two, three = tmp_path / "s1", tmp_path / "s2", tmp_path / "s3"
    _shard(one, _passed(5, "a"))
    _shard(two, _passed(5, "b"), run_id="another-run")
    result, problems = merge_shard_markers(
        [one, two, three], run_id=RUN, lane="browser", out_dir=tmp_path / "m"
    )
    assert result is None
    assert any("from run 'another-run'" in p for p in problems)
    assert any("no run-trust marker" in p for p in problems)


def test_cli_merges_then_checks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUN_TRUST_MIN_EXECUTED", "4")
    one, two, out = tmp_path / "s1", tmp_path / "s2", tmp_path / "m"
    _shard(one, _passed(2, "a"))
    _shard(two, _passed(2, "b"))
    args = ["--dir", str(out), "--lane", "browser", "--run-id", RUN, "--merge", str(one)]
    assert main([*args, str(two)]) == 0
    assert "RUN TRUST OK" in capsys.readouterr().out
    assert main([*args, str(tmp_path / "absent")]) == 2
