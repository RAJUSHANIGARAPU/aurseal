"""The run gate as a pytest plugin: controller-side tally, xdist, reruns, enforcement."""

from __future__ import annotations

import json
import os

import pytest

pytest_plugins = ["pytester"]

# The rewrite warning appears when the outer session has already imported the plugin.
PLUGIN = (
    "-p",
    "gate.pytest_plugin",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
    "-p",
    "no:cacheprovider",
)

MOSTLY_SKIPPED = """
import pytest

def test_ok():
    pass

@pytest.mark.parametrize("n", range(3))
def test_needs_env(n):
    pytest.skip("environment not reachable")
"""


def _marker(pytester, lane="default") -> dict:
    name = "run-trust.json" if lane == "default" else f"run-trust-{lane}.json"
    return json.loads((pytester.path / "reports" / name).read_text())


def _first_line(pytester) -> str:
    return (pytester.path / "reports" / "run-trust.txt").read_text().splitlines()[0]


def test_gate_is_off_unless_asked_for(pytester):
    pytester.makepyfile(MOSTLY_SKIPPED)
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == 0
    assert not (pytester.path / "reports" / "run-trust.txt").exists()


def test_fail_enforcement_turns_a_mostly_skipped_run_red(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makepyfile(MOSTLY_SKIPPED)
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*run trust*", "*RUN TRUST FAILED*", "*SKIP RATIO*"])
    result.stdout.fnmatch_lines(["*environment not reachable*"])
    marker = _marker(pytester)
    assert (marker["selected"], marker["executed"], marker["skipped"]) == (4, 1, 3)
    assert marker["run_id"] == os.environ["TEST_RUN_ID"]


def test_report_enforcement_keeps_the_finding_and_the_exit_code(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "report")
    pytester.makepyfile(MOSTLY_SKIPPED)
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == 0
    assert _first_line(pytester).startswith("VERDICT=FAIL ENFORCEMENT=REPORT")


def test_real_failures_keep_their_own_exit_code(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makepyfile("def test_a():\n    assert False\n")
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert _first_line(pytester).startswith("VERDICT=PASS")


def test_outcomes_are_classified(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "report")
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def broken():
            raise RuntimeError("setup broke")

        @pytest.fixture
        def bad_teardown():
            yield
            raise RuntimeError("teardown broke")

        def test_pass(): pass
        def test_fail(): assert False
        def test_error(broken): pass
        def test_teardown(bad_teardown): pass
        @pytest.mark.xfail(strict=True)
        def test_xfail(): assert False
        @pytest.mark.skip(reason="not today")
        def test_skip(): pass
        """
    )
    pytester.runpytest(*PLUGIN)
    tests = _marker(pytester)["tests"]
    outcomes = {nodeid.split("::")[1]: data["outcome"] for nodeid, data in tests.items()}
    assert outcomes == {
        "test_pass": "passed",
        "test_fail": "failed",
        "test_error": "error",
        "test_teardown": "error",
        "test_xfail": "xfailed",
        "test_skip": "skipped",
    }
    assert tests["test_outcomes_are_classified.py::test_skip"]["reason"] == "not today"


def test_detector_rule_reads_known_defect_outcomes_and_verdict_rows(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.known_defect("BUG-142", raises=AssertionError)
        def test_detector():
            pytest.skip("precondition missing")

        def test_other():
            pass
        """
    )
    result = pytester.runpytest(*PLUGIN, "-p", "verification.pytest_plugin")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*DETECTOR VERDICTS: 0 verdict(s) from 1 selected*"])

    # The same run, now with a verdict row written by the detector layer.
    verdicts = pytester.path / "reports" / "verdicts" / "verdicts.jsonl"
    verdicts.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "run_id": os.environ["TEST_RUN_ID"],
            "nodeid": "x::y",
            "kind": "expected_red",
            "ticket": "BUG-142",
            "verdict": "reproduces",
            "detail": "",
        },
        {"run_id": "another-run", "nodeid": "x::z", "kind": "expected_red", "verdict": "cleared"},
    ]
    verdicts.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    result = pytester.runpytest(*PLUGIN, "-p", "verification.pytest_plugin")
    marker = _marker(pytester)
    assert marker["verdicts_reached"] == 1
    assert marker["detectors_selected"] == 2


def test_known_defect_that_reproduces_is_a_verdict(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.known_defect("BUG-142", raises=AssertionError)
        def test_detector():
            assert 1 == 2
        """
    )
    result = pytester.runpytest(*PLUGIN, "-p", "verification.pytest_plugin")
    assert result.ret == 0
    assert _marker(pytester)["verdicts_reached"] == 1


def test_provenance_rule_uses_recorded_fixture_rows(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makepyfile(
        """
        import pytest
        from gate.provenance import record_fixture

        @pytest.mark.seeded
        def test_on_picked_data():
            pass

        @pytest.mark.seeded
        def test_on_built_data():
            record_fixture("fresh", "orders/basic.json", entity_id="E-1")
        """
    )
    assert pytester.runpytest(*PLUGIN).ret == 0
    assert _marker(pytester)["passed_on_acquired"] == 1

    rows = (pytester.path / "reports" / "data-origin.jsonl").read_text().splitlines()
    assert json.loads(rows[0])["nodeid"].endswith("::test_on_built_data")

    (pytester.path / "reports" / "data-origin.jsonl").unlink()
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seeded
        def test_on_picked_data():
            pass
        """
    )
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(["*FIXTURE PROVENANCE*"])


def test_a_later_invocation_of_the_same_run_merges_instead_of_replacing(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    monkeypatch.setenv("RUN_TRUST_MIN_EXECUTED", "5")
    flag = pytester.path / "fixed"
    pytester.makepyfile(
        f"""
        import os
        import pytest

        @pytest.mark.parametrize("n", range(6))
        def test_many(n):
            pass

        def test_flaky():
            assert os.path.exists({str(flag)!r})
        """
    )
    first = pytester.runpytest(*PLUGIN)
    assert first.ret == pytest.ExitCode.TESTS_FAILED
    flag.write_text("")
    # Re-run only the failed test, as `pytest --lf` would: a one-test tally on its own
    # sits below the floor; merged into its run it is judged on all seven.
    second = pytester.runpytest(
        *PLUGIN,
        "test_a_later_invocation_of_the_same_run_merges_instead_of_replacing.py::test_flaky",
    )
    assert second.ret == 0
    marker = _marker(pytester)
    assert (marker["selected"], marker["executed"]) == (7, 7)
    assert marker["verdict"] == "PASS"

    # A different run id is a different run: the marker is replaced, not merged.
    monkeypatch.setenv("TEST_RUN_ID", "a-new-run")
    pytester.runpytest(
        *PLUGIN,
        "test_a_later_invocation_of_the_same_run_merges_instead_of_replacing.py::test_flaky",
    )
    assert _marker(pytester)["selected"] == 1


def test_rerunfailures_attempts_are_discarded(pytester, monkeypatch):
    pytest.importorskip("pytest_rerunfailures")
    monkeypatch.setenv("RUN_TRUST", "report")
    counter = pytester.path / "attempts"
    pytester.makepyfile(
        f"""
        from pathlib import Path

        def test_flaky():
            p = Path({str(counter)!r})
            n = int(p.read_text()) if p.exists() else 0
            p.write_text(str(n + 1))
            assert n >= 1
        """
    )
    result = pytester.runpytest(*PLUGIN, "--reruns", "2")
    assert result.parseoutcomes() == {"passed": 1, "rerun": 1}
    marker = _marker(pytester)
    assert (marker["selected"], marker["executed"]) == (1, 1)
    entry = list(marker["tests"].values())[0]
    assert entry["outcome"] == "passed"
    # The retried attempt is kept as a fact about the test: it passed only on a rerun.
    assert entry["failed_attempts"] == 1
    assert "assert 0 >= 1" in entry["first_failure"]


def test_xdist_workers_are_tallied_on_the_controller(pytester, monkeypatch, subprocess_env):
    pytest.importorskip("xdist")
    monkeypatch.setenv("RUN_TRUST", "fail")
    monkeypatch.setenv("RUN_TRUST_MIN_EXECUTED", "5")
    monkeypatch.delenv("TEST_RUN_ID")
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(
        """
        import os
        import pytest
        from gate.provenance import record_fixture

        @pytest.mark.parametrize("n", range(8))
        def test_many(n):
            assert os.environ["TEST_RUN_ID"]

        @pytest.mark.seeded
        def test_seeded():
            record_fixture("reused", "pool", candidates=3)

        @pytest.mark.parametrize("n", range(2))
        def test_skip(n):
            pytest.skip("not here")
        """
    )
    result = pytester.runpytest_subprocess(*PLUGIN, "-n", "2")
    assert result.ret == 0, result.stdout.str()
    marker = _marker(pytester)
    assert (marker["selected"], marker["executed"], marker["skipped"]) == (11, 9, 2)
    assert marker["passed_on_acquired"] == 1
    assert marker["verdict"] == "PASS"
    # One run id, generated by the controller and inherited by every worker.
    rows = (pytester.path / "reports" / "data-origin.jsonl").read_text().splitlines()
    assert json.loads(rows[0])["run_id"] == marker["run_id"]
    # Workers do not write markers of their own.
    assert sorted(p.name for p in (pytester.path / "reports").glob("run-trust*")) == [
        "run-trust.json",
        "run-trust.txt",
    ]


def test_selected_but_never_reported_counts_as_silence(pytester, monkeypatch, subprocess_env):
    monkeypatch.setenv("RUN_TRUST", "report")
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(
        """
        def test_a():
            pass

        def test_b():
            raise KeyboardInterrupt

        def test_c():
            pass
        """
    )
    result = pytester.runpytest_subprocess(*PLUGIN)
    assert result.ret == pytest.ExitCode.INTERRUPTED
    tests = _marker(pytester)["tests"]
    assert len(tests) == 3
    assert tests["test_selected_but_never_reported_counts_as_silence.py::test_c"]["outcome"] == (
        "not_run"
    )


def test_lane_marker_name(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "report")
    monkeypatch.setenv("RUN_TRUST_LANE", "unit")
    pytester.makepyfile("def test_a():\n    pass\n")
    pytester.runpytest(*PLUGIN)
    assert _marker(pytester, "unit")["lane"] == "unit"


def test_markers_are_registered_for_strict_mode(pytester):
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.unit
        @pytest.mark.on_demand
        @pytest.mark.e2e
        @pytest.mark.stateful
        @pytest.mark.slow
        @pytest.mark.seeded
        @pytest.mark.journey_group("checkout")
        def test_a():
            pass
        """
    )
    pytester.runpytest(*PLUGIN, "--strict-markers").assert_outcomes(passed=1)


def test_plugin_abstains_every_later_test_after_a_trip(pytester, monkeypatch, subprocess_env):
    monkeypatch.setenv("CIRCUIT_BREAKER_HOSTS", "api.example.test")
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(
        """
        def test_first(circuit_breaker):
            circuit_breaker.record("api", "GET", "https://api.example.test/health", 503)

        def test_second():
            pass

        def test_third():
            pass
        """
    )
    result = pytester.runpytest_subprocess(
        "-p", "gate.pytest_plugin", "-p", "no:cacheprovider", "-rs"
    )
    result.stdout.fnmatch_lines(["*already returned a 5xx in this run*"])
    assert result.parseoutcomes() == {"passed": 1, "skipped": 2}
    # Abstention is silence, and the run-trust gate says so instead of going green.
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_collect_only_is_not_judged(pytester, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "fail")
    pytester.makepyfile(MOSTLY_SKIPPED)
    result = pytester.runpytest(*PLUGIN, "--collect-only", "-q")
    assert result.ret == 0
    assert not (pytester.path / "reports" / "run-trust.txt").exists()
