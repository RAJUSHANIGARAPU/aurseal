"""
A dry run that has already written the file is not a dry run.

``synthetic_data_edge`` once created ``ai_generated_tests/synthetic/`` and wrote
its profiles above its ``if is_dry:`` branch, then returned a preview describing
what it would do. It also generated a Playwright test whose body was a skip:
twenty test ids over records that were never sent anywhere. The plugin now
probes a target with the records instead, and writes no test stub at all.
"""

from __future__ import annotations

import json

import pytest

from tests.plugins._tier4 import load
from tests.plugins.test_synthetic_honest_status import PROFILE, FakeHttp


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """The output path is cwd-relative, so chdir is what isolates the run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    return tmp_path


def _plugin():
    return load("synthetic_data_edge").SyntheticDataEdgePlugin()


def _output(workspace):
    return workspace / "ai_generated_tests" / "synthetic"


def _context(**extra):
    return {"profile": PROFILE, "target": "http://127.0.0.1:9", "http": FakeHttp(), **extra}


class TestADryRunTouchesNothing:
    def test_it_writes_no_files(self, workspace):
        result = _plugin().dry_run(_context())

        assert not _output(workspace).exists()
        assert not (workspace / "metrics").exists()
        assert result.dry_run is True

    def test_it_does_not_overwrite_an_existing_profiles_file(self, workspace):
        out = _output(workspace)
        out.mkdir(parents=True)
        (out / "synthetic_profiles.json").write_text('["kept"]', encoding="utf-8")

        _plugin().dry_run(_context())

        assert (out / "synthetic_profiles.json").read_text() == '["kept"]'

    def test_it_is_scored_neither_way(self, workspace):
        assert _plugin().dry_run(_context()).status == "skip"


class TestNoStubIsWritten:
    def test_a_real_run_writes_no_test_file(self, workspace):
        _plugin().run(_context())

        assert not (_output(workspace) / "playwright_tests").exists()
        assert not list(workspace.rglob("test_*.py"))


class TestItStillProbesTheExtremes:
    """Positive controls: a plugin that wrote nothing at all would pass every
    dry-run test above."""

    def test_a_real_run_writes_records_and_verdicts(self, workspace):
        result = _plugin().run(_context())

        records = json.loads((_output(workspace) / "synthetic_profiles.json").read_text())
        assert len(records) == result.findings[0]["probed"] > 0
        assert {r["mode"] for r in records} == {"valid", "boundary", "invalid"}

    def test_the_records_are_still_extreme(self, workspace):
        _plugin().run(_context())

        records = json.loads((_output(workspace) / "synthetic_profiles.json").read_text())
        names = [r["values"].get("name", "") for r in records]
        assert "A" * 256 in names
        assert any("‮" in str(name) for name in names), "the RTL override record is gone"
