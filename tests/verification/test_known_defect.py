"""Tests for the known_defect marker, run in an isolated pytest subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from verification.pytest_plugin import KnownDefectPlugin

ROOT = Path(__file__).resolve().parents[2]

TESTS = textwrap.dedent(
    """
    import pytest

    @pytest.mark.known_defect("BUG-1", reason="totals off by one", raises=AssertionError)
    def test_still_broken():
        assert 1 + 1 == 3

    @pytest.mark.known_defect("BUG-2", raises=AssertionError)
    def test_quietly_fixed():
        assert 1 + 1 == 2

    @pytest.mark.known_defect("BUG-3", raises=AssertionError)
    def test_fails_for_another_reason():
        raise KeyError("fixture typo")

    def test_unmarked():
        assert True
    """
)


def _run(tmp_path: Path, source: str, closed: str = "") -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_sample.py").write_text(source)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "KNOWN_DEFECTS_CLOSED": closed}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "verification.pytest_plugin"]
        + ["-p", "no:cacheprovider", "-rA", "-q", str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_open_defects_invert_and_unexpected_pass_fails_loudly(tmp_path):
    out = _run(tmp_path, TESTS).stdout
    assert "2 failed, 1 passed, 1 xfailed" in out, out
    assert "XFAIL test_sample.py::test_still_broken" in out
    assert "known defect BUG-1 is open: totals off by one" in out
    # The graduation signal: the fixed defect is named in the failure.
    assert "[XPASS(strict)] known defect BUG-2 is open" in out
    assert "BUG-2 looks fixed" in out
    # raises= keeps an unrelated failure from hiding behind the marker.
    assert "FAILED test_sample.py::test_fails_for_another_reason" in out


def test_closed_defect_runs_as_an_ordinary_test(tmp_path):
    out = _run(tmp_path, TESTS, closed="BUG-1, BUG-2").stdout
    assert "FAILED test_sample.py::test_still_broken" in out, out
    assert "PASSED test_sample.py::test_quietly_fixed" in out
    assert "xfailed" not in out


def test_marker_without_defect_id_is_a_usage_error(tmp_path):
    source = "import pytest\n\n@pytest.mark.known_defect()\ndef test_x():\n    pass\n"
    result = _run(tmp_path, source)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "needs a defect id" in result.stderr


def test_custom_resolver_decides_open_state():
    plugin = KnownDefectPlugin(is_open=lambda defect_id: defect_id.startswith("OPEN"))
    assert plugin._is_open("OPEN-7") and not plugin._is_open("DONE-7")
    assert not KnownDefectPlugin.from_closed_ids([" X-1 ", ""])._is_open("X-1")


MATCHED = textwrap.dedent(
    """
    import pytest

    SIG = r"totals differ by one cent"

    @pytest.mark.known_defect("BUG-1", raises=AssertionError, match=SIG)
    def test_documented():
        assert False, "BUG-1: totals differ by one cent"

    @pytest.mark.known_defect("BUG-2", raises=AssertionError, match=SIG)
    def test_undocumented():
        assert False, "BUG-2: could not open the order"

    @pytest.mark.known_defect("BUG-3", raises=AssertionError, match=SIG)
    def test_fixed():
        assert True

    @pytest.mark.known_defect("BUG-4", raises=AssertionError, match=SIG)
    def test_skipped():
        pytest.skip("no order in this environment")

    @pytest.mark.known_defect("BUG-5", raises=AssertionError, match=SIG)
    def test_wrong_exception():
        raise KeyError("fixture typo")
    """
)


def _verdicts(path: Path) -> dict[str, str]:
    import json

    return {
        json.loads(line)["ticket"]: json.loads(line)["verdict"]
        for line in path.read_text().splitlines()
    }


def test_match_turns_an_undocumented_failure_red_and_records_verdicts(tmp_path, isolated_verdicts):
    out = _run(tmp_path, MATCHED).stdout
    assert "3 failed, 1 skipped, 1 xfailed" in out, out
    assert "XFAIL test_sample.py::test_documented" in out
    assert "FAILED test_sample.py::test_undocumented" in out
    assert "UNDOCUMENTED reason" in out
    assert _verdicts(isolated_verdicts) == {
        "BUG-1": "reproduces",
        "BUG-2": "signature_mismatch",
        "BUG-3": "cleared",
        "BUG-4": "held_empty",
        "BUG-5": "signature_mismatch",
    }


def test_closed_defects_record_closed_verdicts(tmp_path, isolated_verdicts):
    out = _run(tmp_path, MATCHED, closed="BUG-1,BUG-2,BUG-3").stdout
    assert "FAILED test_sample.py::test_documented" in out, out
    assert "PASSED test_sample.py::test_fixed" in out
    verdicts = _verdicts(isolated_verdicts)
    assert verdicts["BUG-1"] == "closed_regressed"
    assert verdicts["BUG-2"] == "signature_mismatch"
    assert verdicts["BUG-3"] == "closed_pass"


@pytest.mark.parametrize("match", ["BUG-1", "BUG-1: !!", "short"])
def test_loose_match_is_a_usage_error(tmp_path, match):
    source = (
        "import pytest\n\n"
        f"@pytest.mark.known_defect('BUG-1', match={match!r})\n"
        "def test_x():\n    assert False\n"
    )
    result = _run(tmp_path, source)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "too loose" in result.stderr


def test_failing_fixture_is_not_exercised(tmp_path, isolated_verdicts):
    source = textwrap.dedent(
        """
        import pytest

        @pytest.fixture
        def order():
            raise RuntimeError("database down")

        @pytest.mark.known_defect("BUG-1", raises=AssertionError)
        def test_x(order):
            assert False
        """
    )
    _run(tmp_path, source)
    assert _verdicts(isolated_verdicts) == {"BUG-1": "held_empty"}


BROWSER_SCOPED = textwrap.dedent(
    """
    import pytest

    # The engine is read from the node id here; pytest-playwright owns `browser_name`.
    ENGINES = pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])

    @ENGINES
    @pytest.mark.known_defect("BUG-9", raises=AssertionError, browsers=("webkit",))
    def test_layout(engine):
        assert engine != "webkit"

    @ENGINES
    @pytest.mark.known_defect("BUG-10", raises=AssertionError, browsers=["webkit"])
    def test_broken_everywhere(engine):
        assert False
    """
)


def test_browser_scoped_defect_applies_only_on_its_engine(tmp_path):
    out = _run(tmp_path, BROWSER_SCOPED).stdout
    assert "XFAIL test_sample.py::test_layout[webkit]" in out, out
    assert "PASSED test_sample.py::test_layout[chromium]" in out
    assert "PASSED test_sample.py::test_layout[firefox]" in out
    # Negative control: out of scope, a failure is a plain failure, not hidden by the marker.
    assert "FAILED test_sample.py::test_broken_everywhere[chromium]" in out
    assert "FAILED test_sample.py::test_broken_everywhere[firefox]" in out
    assert "XFAIL test_sample.py::test_broken_everywhere[webkit]" in out


def test_unknown_browser_scope_is_a_usage_error(tmp_path):
    source = textwrap.dedent(
        """
        import pytest

        @pytest.mark.known_defect("BUG-11", browsers=("safari",))
        def test_x():
            pass
        """
    )
    result = _run(tmp_path, source)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "browsers=" in result.stderr
