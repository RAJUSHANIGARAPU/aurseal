"""
A dry run that has already written the file is not a dry run.

``synthetic_data_edge`` created ``ai_generated_tests/synthetic/`` and wrote the
profiles four lines *above* its ``if is_dry:`` branch, then returned a preview
describing what it would do. Every sibling plugin guards the write; this one
performed it and reported it as a plan.

Its generated test was ``assert profile is not None`` over a parametrised list
of dicts — twenty green tests named after edge cases that had never been sent
anywhere. And when Faker was missing the fallback produced
``profile_0 … profile_19``, with none of the long names, RTL overrides or
zero-width characters the plugin exists to produce, reported identically to a
good run.
"""

from __future__ import annotations

import json
import sys

import pytest

from tests.plugins._tier4 import load


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """The output path is cwd-relative, so chdir is what isolates the run."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _plugin():
    return load("synthetic_data_edge").SyntheticDataEdgePlugin()


def _output(workspace):
    return workspace / "ai_generated_tests" / "synthetic"


class TestADryRunTouchesNothing:
    def test_it_writes_no_files(self, workspace):
        result = _plugin().dry_run({})

        assert not _output(workspace).exists()
        assert result.dry_run is True

    def test_it_does_not_overwrite_an_existing_profiles_file(self, workspace):
        out = _output(workspace)
        out.mkdir(parents=True)
        (out / "synthetic_profiles.json").write_text('["kept"]', encoding="utf-8")

        _plugin().dry_run({})

        assert (out / "synthetic_profiles.json").read_text() == '["kept"]'

    def test_it_is_scored_neither_way(self, workspace):
        assert _plugin().dry_run({}).status == "skip"


class TestTheGeneratedStubCannotBeMistakenForCoverage:
    def test_it_skips_instead_of_asserting_not_none(self, workspace):
        _plugin().run({})

        body = (_output(workspace) / "playwright_tests" / "test_synthetic_profiles.py").read_text()
        assert "assert profile is not None" not in body
        assert "pytest.skip" in body


class TestItStillGenerates:
    """Positive controls: a plugin that wrote nothing at all would pass every
    dry-run test above."""

    def test_a_real_run_writes_the_profiles(self, workspace):
        result = _plugin().run({})

        profiles = json.loads((_output(workspace) / "synthetic_profiles.json").read_text())
        assert len(profiles) == 20
        assert result.findings[0]["profiles_generated"] == 20

    def test_the_profiles_are_still_extreme(self, workspace):
        _plugin().run({})

        profiles = json.loads((_output(workspace) / "synthetic_profiles.json").read_text())
        names = [p.get("name", "") for p in profiles]
        assert "A" * 256 in names
        assert any("‮" in name for name in names), "the RTL override profile is gone"

    def test_a_real_run_is_scored_neither_way(self, workspace):
        """Writing fixtures is not a verdict about the product."""
        assert _plugin().run({}).status == "skip"


class TestADegradedGeneratorSaysSo:
    def test_a_missing_faker_reaches_no_verdict(self, workspace, monkeypatch):
        """The fallback profiles carry no edge cases, and used to report the
        same ``profiles_generated: 20`` as a good run."""
        monkeypatch.setitem(sys.modules, "faker", None)

        result = _plugin().run({})

        assert result.status == "unknown"
        assert result.findings[0]["faker_available"] is False
