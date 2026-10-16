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
