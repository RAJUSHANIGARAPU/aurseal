"""The mutation engine offline: config, parsing mutmut 3 output, the score and its threshold.

``tests/checks/fixtures/mutmut-results.txt`` is a slice of real ``mutmut results --all true``
output from mutmut 3.8.0. mutmut is never started here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from checks._sink import MemorySink
from checks.mutation import mutmut
from checks.mutation.config import DEFAULT_CONFIG, MutationConfig, PackageScope
from checks.mutation.judge import judge, score
from checks.mutation.mutmut import MutationCounts, MutmutRun, parse_results
from checks.mutation.runner import run_mutation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RESULTS = (FIXTURES / "mutmut-results.txt").read_text(encoding="utf-8")


def _counts(killed=0, survived=0, timeout=0, no_tests=0, **kw) -> MutationCounts:
    return MutationCounts(
        killed=killed, survived=survived, timeout=timeout, no_tests=no_tests, **kw
    )


class TestParse:
    def test_recorded_output(self):
        counts = parse_results(RESULTS)
        assert (counts.killed, counts.survived, counts.total) == (8, 4, 12)
        assert "checks._outcome.xǁCheckOutcomeǁok__mutmut_1" in counts.survivors

    def test_progress_and_emoji_noise_does_not_change_counts(self):
        """The old wrapper took the first integer on any line containing 'survived'."""
        noisy = "\n".join(
            [
                "⠸ 96/96  🎉 54 🫥 0  ⏰ 0  🤔 0  🙁 42  🔇 0  🧙 0",
                "42 survived, 54 killed",
                "21.81 mutations/second",
                RESULTS,
                "warning: something: survived",
            ]
        )
        counts = parse_results(noisy)
        assert (counts.killed, counts.survived) == (8, 4)

    def test_every_status_is_counted(self):
        text = "\n".join(
            f"    pkg.mod.x_f__mutmut_{i}: {status}" for i, status in enumerate(mutmut.STATUSES)
        )
        counts = parse_results(text)
        assert counts.total == len(mutmut.STATUSES)
        assert counts.no_tests == counts.not_checked == counts.interrupted == 1


class TestScore:
    def test_timeouts_count_as_killed(self):
        assert score(_counts(killed=1, timeout=1, survived=2)) == 0.5

    def test_no_tests_mutants_lower_the_score(self):
        assert score(_counts(killed=3, no_tests=1)) == 0.75

    def test_skipped_and_suspicious_are_left_out(self):
        assert score(_counts(killed=1, survived=1, skipped=5, suspicious=5)) == 0.5

    def test_no_scored_mutants_has_no_score(self):
        assert score(_counts(skipped=2)) is None


class TestJudge:
    def test_exactly_at_threshold_passes(self):
        out = judge({"core": _counts(killed=7, survived=3)}, {"core": 0.70})
        assert out.status == "pass"

    def test_just_below_fails_naming_the_package_and_survivors(self):
        counts = _counts(killed=69, survived=31, survivors=["core.x_f__mutmut_2"])
        out = judge({"core": counts}, {"core": 0.70})
        assert out.status == "fail"
        assert "core 69.00% < 70.00%" in out.reason and "core.x_f__mutmut_2" in out.reason

    def test_one_package_below_fails_the_run(self):
        out = judge(
            {"a": _counts(killed=9, survived=1), "b": _counts(killed=1, survived=9)},
            {"a": 0.5, "b": 0.5},
        )
        assert out.status == "fail" and "b " in out.reason and "a " not in out.reason

    def test_zero_mutants_is_unknown(self):
        assert judge({"a": _counts()}, {"a": 0.5}).status == "unknown"

    def test_unchecked_mutants_are_unknown(self):
        assert judge({"a": _counts(killed=9, not_checked=1)}, {"a": 0.5}).status == "unknown"

    def test_a_package_without_results_is_unknown(self):
        out = judge({"a": None}, {"a": 0.5}, reasons={"a": "mutmut run exited 1"})
        assert out.status == "unknown" and "exited 1" in out.reason

    def test_survivors_are_never_a_warn(self):
        out = judge({"a": _counts(killed=1, survived=1)}, {"a": 0.9})
        assert out.status == "fail"


class TestConfig:
    def _write(self, tmp_path, body: str) -> Path:
        path = tmp_path / "pyproject.toml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_thresholds_from_a_pyproject(self, tmp_path):
        path = self._write(
            tmp_path,
            "[tool.wiki_checks.mutation]\nmax_children = 2\n"
            "thresholds = {core = 0.8}\n"
            "[tool.wiki_checks.mutation.packages.core]\n"
            'source_paths = ["core"]\ntests = ["tests/core"]\nthreshold = 0.6\n'
            "[tool.wiki_checks.mutation.packages.gate]\n"
            'source_paths = ["gate"]\ntests = ["tests/gate"]\nthreshold = 0.7\n',
        )
        config = MutationConfig.from_pyproject(path)
        assert config.thresholds == {"core": 0.8, "gate": 0.7}
        assert config.max_children == 2
        assert [p.name for p in config.select(["gate"])] == ["gate"]
        with pytest.raises(ValueError):
            config.select(["nope"])

    @pytest.mark.parametrize(
        "body",
        [
            "[tool.other]\nx = 1\n",
            "[tool.wiki_checks.mutation]\n",
            '[tool.wiki_checks.mutation.packages.a]\nsource_paths = ["a"]\ntests = ["t"]\n',
            '[tool.wiki_checks.mutation.packages.a]\nsource_paths = ["a"]\ntests = ["t"]\n'
            "threshold = 2\n",
            "[tool.wiki_checks.mutation.packages.a]\nthreshold = 0.5\n",
            "not toml [",
        ],
    )
    def test_unusable_configs_are_rejected(self, tmp_path, body):
        with pytest.raises(ValueError):
            MutationConfig.from_pyproject(self._write(tmp_path, body))

    def test_the_committed_config_loads_with_real_thresholds(self):
        config = MutationConfig.from_pyproject(DEFAULT_CONFIG)
        assert {"checks", "verification", "gate", "contract_differ"} <= set(config.thresholds)
        assert all(t > 0 for t in config.thresholds.values()), "a 0 threshold can never fail"

    def test_the_generated_mutmut_table_uses_the_3x_keys(self):
        config = MutationConfig.from_pyproject(DEFAULT_CONFIG)
        table = mutmut.mutmut_table(config.select(["checks"])[0], config)
        assert "source_paths = " in table and "pytest_add_cli_args_test_selection" in table
        assert "paths_to_mutate" not in table and "--disable-mutation-types" not in table
        assert '"addopts="' in table


class TestWorkdir:
    def test_the_copy_leaves_the_repo_alone_and_skips_heavy_dirs(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "pkg").mkdir(parents=True)
        (repo / "pkg" / "m.py").write_text("x = 1\n")
        (repo / "reports").mkdir()
        (repo / ".venv").mkdir()
        (repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        scope = PackageScope("pkg", ("pkg",), ("tests",), 0.5)
        config = MutationConfig(packages=(scope,))
        work = mutmut.prepare_workdir(repo, tmp_path / "work", scope, config)
        assert (work / "pkg" / "m.py").is_file()
        assert not (work / "reports").exists() and not (work / ".venv").exists()
        assert "[tool.mutmut]" in (work / "pyproject.toml").read_text()
        assert "[tool.mutmut]" not in (repo / "pyproject.toml").read_text()


class FakeRunner:
    def __init__(self, results: dict[str, MutmutRun]) -> None:
        self.results = results

    def run(self, config, scope):  # noqa: ANN001
        return self.results[scope.name]


def _config() -> MutationConfig:
    return MutationConfig(packages=(PackageScope("core", ("core",), ("tests/core",), 0.5),))


class TestRunner:
    def test_measured_scores_are_recorded(self):
        sink = MemorySink()
        counts = _counts(killed=3, survived=1, survivors=["core.x_f__mutmut_1"])
        result = MutmutRun("core", True, counts=counts, tool_version="3.8.0")
        out = run_mutation(_config(), runner=FakeRunner({"core": result}), sink=sink)
        assert out.status == "pass"
        assert sink.rows["metric_mutation_score"][0]["score"] == 0.75
        assert sink.rows["metric_mutation_survivor"][0]["mutant_id"] == "core.x_f__mutmut_1"

    def test_a_failed_mutmut_run_is_unknown(self):
        result = MutmutRun("core", False, reason="mutmut run exited 1")
        assert run_mutation(_config(), runner=FakeRunner({"core": result})).status == "unknown"

    def test_mutmut_missing_is_unknown(self, monkeypatch):
        monkeypatch.setattr(mutmut, "mutmut_version", lambda: None)
        assert run_mutation(_config()).status == "unknown"

    def test_an_unknown_package_is_an_error(self):
        assert run_mutation(_config(), ["nope"]).status == "error"
