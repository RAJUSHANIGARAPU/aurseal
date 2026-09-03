"""An outage must not leave a plausible-looking artefact behind.

``chaos-resilience`` produces nothing but model text, so when the model was
unreachable it had nothing to write — and wrote five copies of "Network
partition between services" anyway, to the real output path, and returned
``pass``. On disk that is indistinguishable from a real answer, which makes it
worse than an empty directory: it is quotable.

The controls below are load-bearing. "Writes nothing on an outage" is trivially
satisfied by a plugin that writes nothing ever, so ``TestItStillGenerates``
pins the healthy path: real scenarios parsed out of a real reply, written to
the file, counted correctly, and the model demonstrably asked.

No network: ``StubGovernor`` replaces the one call site.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.plugins._tier3_support import StubGovernor, load_plugin, write_source_tree

_OUT = Path("ai_generated_tests/chaos/chaos_scenarios.md")

_GOOD_REPLY = "\n".join(
    f"## Scenario {n}: Broker {n} stops accepting connections\n"
    f"The message broker refuses new sessions.\n"
    f"**Fault**: drop port 5672 for 60s\n"
    f"**Expected behavior**: producers buffer and retry\n"
    for n in range(1, 6)
)

_SOURCE = {
    "pkg/__init__.py": "",
    "pkg/service.py": "import json\nfrom pkg import helper\n",
    "pkg/helper.py": "import os\n",
}


@pytest.fixture
def plugin():
    return load_plugin("chaos_resilience.plugin.py", "ChaosResiliencePlugin")


@pytest.fixture
def source_dir(tmp_path, monkeypatch):
    """A tree with real cross-module imports, with cwd moved off the repo.

    ``_OUT_FILE`` is cwd-relative, so the chdir is what keeps a test run from
    writing into the repo's own ``ai_generated_tests/``.
    """
    monkeypatch.chdir(tmp_path)
    return write_source_tree(tmp_path / "src", _SOURCE)


def _run(plugin, source_dir, governor, **overrides):
    return plugin.run({"source_dir": str(source_dir), "cost_governor": governor, **overrides})


class TestAnOutageProducesNoScenarios:
    @pytest.mark.parametrize("reply", ["", "   ", "\n"])
    def test_an_unreachable_model_is_unknown(self, plugin, source_dir, reply):
        result = _run(plugin, source_dir, StubGovernor(reply))
        assert result.status == "unknown"

    def test_nothing_is_written(self, plugin, source_dir):
        _run(plugin, source_dir, StubGovernor(""))
        assert not _OUT.exists(), "an outage left an artefact that reads as a real answer"

    def test_no_canned_scenario_is_reported(self, plugin, source_dir):
        result = _run(plugin, source_dir, StubGovernor(""))
        assert "Network partition between services" not in str(result.findings)
        assert "Service dependency failure" not in str(result.findings)

    def test_the_reason_is_named(self, plugin, source_dir):
        result = _run(plugin, source_dir, StubGovernor(""))
        assert "no scenarios were generated" in result.findings[0]["reason"]


class TestAnUnusableReplyIsNotScenarios:
    def test_a_reply_without_headings_is_unknown(self, plugin, source_dir):
        """The model answered — just not with what was asked for. Splitting it
        on "\\n## " produced one "scenario" that was the refusal itself."""
        result = _run(plugin, source_dir, StubGovernor("I can't help with that request."))
        assert result.status == "unknown"
        assert not _OUT.exists()

    def test_an_empty_source_tree_is_unknown_and_asks_nothing(self, plugin, tmp_path, monkeypatch):
        """ "Project has 0 modules" is not an architecture summary. Paying a
        model to invent scenarios for it is the second problem."""
        monkeypatch.chdir(tmp_path)
        empty = tmp_path / "empty"
        empty.mkdir()
        governor = StubGovernor(_GOOD_REPLY)
        result = _run(plugin, empty, governor)
        assert result.status == "unknown"
        assert governor.calls == 0


class TestItStillGenerates:
    """Positive controls for every test above."""

    def test_a_real_reply_passes_and_is_written(self, plugin, source_dir):
        result = _run(plugin, source_dir, StubGovernor(_GOOD_REPLY))
        assert result.status == "pass"
        assert result.findings[0]["scenario_count"] == 5
        assert _OUT.exists()

    def test_every_scenario_survives_to_the_file(self, plugin, source_dir):
        _run(plugin, source_dir, StubGovernor(_GOOD_REPLY))
        content = _OUT.read_text(encoding="utf-8")
        for n in range(1, 6):
            assert f"## Scenario {n}:" in content
            assert f"Broker {n} stops accepting connections" in content

    def test_the_model_was_actually_asked(self, plugin, source_dir):
        governor = StubGovernor(_GOOD_REPLY)
        _run(plugin, source_dir, governor)
        assert governor.calls == 1
        assert "chaos" in governor.prompts[0].lower()

    def test_a_dry_run_reaches_a_verdict_without_writing(self, plugin, source_dir):
        result = _run(plugin, source_dir, StubGovernor(_GOOD_REPLY), dry_run=True)
        assert result.status == "pass"
        assert result.dry_run is True
        assert not _OUT.exists()


class TestTheVirtualenvIsNotTheArchitecture:
    def test_site_packages_is_not_walked(self, plugin, tmp_path, monkeypatch):
        """``source_dir`` defaults to ``.``; unbounded rglob from there pulled
        thousands of dependency modules into the "architecture summary" and
        into the time budget of a plugin retried three times."""
        monkeypatch.chdir(tmp_path)
        root = write_source_tree(
            tmp_path / "src",
            {
                **_SOURCE,
                ".venv/lib/python3.12/site-packages/dep/__init__.py": "import ssl\n",
                ".venv/lib/python3.12/site-packages/dep/core.py": "import socket\n",
                "node_modules/thing/setup.py": "import distutils\n",
                "__pycache__/stale.py": "import pickle\n",
            },
        )
        governor = StubGovernor(_GOOD_REPLY)
        result = _run(plugin, root, governor)
        assert result.findings[0]["module_count"] == 2, "the virtualenv was counted as architecture"
