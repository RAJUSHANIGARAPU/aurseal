"""
"pytest exited non-zero" is not the same claim as "the product is broken".

``e2e_playwright`` and ``api_contract`` both read ``returncode == 0`` as ``pass``
and everything else as ``fail``. Two of pytest's exit codes make that a lie in
opposite directions: exit 5 is "no tests were collected" — a suite that ran and
verified nothing — and exit 4 is "pytest rejected your command line", which
``api_contract`` reached whenever the orchestrator was launched from outside the
repository root, since its test path was relative.

``e2e_playwright`` also answered ``skip`` for a missing test directory. Under the
status vocabulary ``skip`` leaves the health fraction entirely, and on a
``ui_change`` trigger this plugin is the whole HIGH tier — so a wrong working
directory bought 35 points having driven no browser.

Every "reports no verdict" test here is paired with a control proving the plugin
still returns ``pass`` on a green suite and ``fail`` on a real one.
"""

from __future__ import annotations

import subprocess

import pytest

from plugins.tier1._paths import REPO_ROOT
from tests.plugins._tier1 import FakePytest, load, write_module

SUITE = "def test_ok():\n    assert True\n"


@pytest.fixture
def e2e():
    return load("e2e_playwright").E2EPlaywrightPlugin()


@pytest.fixture
def api_contract():
    return load("api_contract").ApiContractPlugin()


@pytest.fixture
def suite_dir(tmp_path):
    """A directory holding one collectable test file."""
    directory = tmp_path / "suite"
    write_module(directory, "test_thing.py", SUITE)
    return directory


class TestE2ECouldNotRunIsNotNotApplicable:
    def test_a_missing_test_directory_is_unknown(self, e2e, tmp_path):
        """It returned `skip`, which drops the HIGH tier out of the score."""
        result = e2e.run({"ui_tests_dir": str(tmp_path / "nope")})

        assert result.status == "unknown"

    def test_an_empty_test_directory_is_unknown(self, e2e, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()

        result = e2e.run({"ui_tests_dir": str(empty)})

        assert result.status == "unknown"

    def test_a_timeout_reaches_no_verdict(self, e2e, suite_dir, monkeypatch):
        """The suite did not finish; the plugin did not break."""
        FakePytest(raises=subprocess.TimeoutExpired(cmd="pytest", timeout=300)).install(monkeypatch)

        result = e2e.run({"ui_tests_dir": str(suite_dir)})

        assert result.status == "unknown"

    def test_a_launch_failure_is_still_an_error(self, e2e, suite_dir, monkeypatch):
        """The control for the timeout above: a broken plugin is `error`."""
        FakePytest(raises=OSError("no such executable")).install(monkeypatch)

        result = e2e.run({"ui_tests_dir": str(suite_dir)})

        assert result.status == "error"


class TestE2EExitCodesMeanWhatTheySay:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (0, "pass"),
            (1, "fail"),
            (2, "error"),
            (3, "error"),
            (4, "error"),
            (5, "unknown"),
            (99, "unknown"),
        ],
    )
    def test_the_exit_code_maps_to_a_verdict(self, e2e, suite_dir, monkeypatch, code, expected):
        FakePytest(returncode=code).install(monkeypatch)

        result = e2e.run({"ui_tests_dir": str(suite_dir)})

        assert result.status == expected
        assert result.findings[0]["exit_code"] == code
        assert result.findings[0]["reason"]

    def test_a_green_suite_is_still_a_pass(self, e2e, suite_dir, monkeypatch):
        """Positive control: a plugin hardwired to `unknown` passes the rest."""
        FakePytest(returncode=0).install(monkeypatch)

        result = e2e.run({"ui_tests_dir": str(suite_dir)})

        assert result.status == "pass"

    def test_pytest_was_actually_pointed_at_the_suite(self, e2e, suite_dir, monkeypatch):
        fake = FakePytest(returncode=0).install(monkeypatch)

        e2e.run({"ui_tests_dir": str(suite_dir)})

        assert fake.target == str(suite_dir)


class TestE2EIsAnchoredToTheRepository:
    def test_the_default_directory_does_not_move_with_the_cwd(self, e2e, tmp_path, monkeypatch):
        """Resolved against `.`, the same run scanned a different tree depending
        on where it was launched from — and reported `skip` when it found none."""
        monkeypatch.chdir(tmp_path)
        fake = FakePytest(returncode=0).install(monkeypatch)

        result = e2e.run({})

        assert result.status == "pass"
        assert fake.target == str(REPO_ROOT / "ui" / "tests")

    def test_a_dry_run_verifies_nothing_and_says_so(self, e2e, suite_dir):
        result = e2e.run({"ui_tests_dir": str(suite_dir), "dry_run": True})

        assert result.status == "skip"


class TestApiContractDoesNotInventContractBreaks:
    def test_a_missing_test_path_is_unknown(self, api_contract, tmp_path, monkeypatch):
        """A relative default plus a wrong cwd gave pytest exit 4, and that was
        published as a broken API contract."""
        FakePytest(returncode=4).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(tmp_path / "nope")})

        assert result.status == "unknown"

    def test_an_empty_test_path_is_unknown(self, api_contract, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        FakePytest(returncode=5).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(empty)})

        assert result.status == "unknown"

    def test_collecting_no_tests_is_unknown(self, api_contract, suite_dir, monkeypatch):
        FakePytest(returncode=5).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(suite_dir)})

        assert result.status == "unknown"

    def test_a_usage_error_is_the_harness_not_the_contract(
        self, api_contract, suite_dir, monkeypatch
    ):
        FakePytest(returncode=4).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(suite_dir)})

        assert result.status == "error"

    def test_a_timeout_reaches_no_verdict(self, api_contract, suite_dir, monkeypatch):
        FakePytest(raises=subprocess.TimeoutExpired(cmd="pytest", timeout=120)).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(suite_dir)})

        assert result.status == "unknown"


class TestApiContractStillReportsRealResults:
    """Positive controls: the plugin must keep both verdicts it is for."""

    def test_a_green_contract_suite_is_a_pass(self, api_contract, suite_dir, monkeypatch):
        FakePytest(returncode=0).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(suite_dir)})

        assert result.status == "pass"
        assert result.findings[0]["test_files"] == 1

    def test_a_broken_contract_is_still_a_fail(self, api_contract, suite_dir, monkeypatch):
        FakePytest(returncode=1).install(monkeypatch)

        result = api_contract.run({"contract_test_path": str(suite_dir)})

        assert result.status == "fail"

    def test_the_default_test_path_does_not_move_with_the_cwd(
        self, api_contract, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        fake = FakePytest(returncode=0).install(monkeypatch)

        result = api_contract.run({})

        assert result.status == "pass"
        assert fake.target == str(REPO_ROOT / "tests" / "contract_testing")

    def test_a_dry_run_verifies_nothing_and_says_so(self, api_contract, suite_dir):
        result = api_contract.run({"contract_test_path": str(suite_dir), "dry_run": True})

        assert result.status == "skip"
