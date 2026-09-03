"""
An empty result set is not a clean bill of health.

``llm_output_oracle`` set ``assessment = "stable"`` and only reassigned it
inside ``if log_dir.exists():`` and then ``if log_files:``. On any checkout that
has never written a trace — every CI container, every fresh clone — it reported
LLM output stable having read no output at all.

``status`` was a literal ``"pass"`` besides, so even a genuine ``"variable"``
assessment, or the ``"unknown"`` a previous fix had already introduced, reached
the orchestrator as green. The finding disagreed with the score, and only the
score gates a deploy.

And the walk that produced its file list was ``rglob("*.py")`` from ``"."``,
which in this checkout is 3573 files, 3353 of them vendored dependencies.
"""

from __future__ import annotations

import pytest

from tests.plugins._tier4 import StubGovernor, load, venv_tree


@pytest.fixture
def sources(tmp_path):
    """One real LLM-calling module, plus a vendored one that must be ignored."""
    (tmp_path / "agent.py").write_text(
        "from api.llm.claude_client import ClaudeLLMClient\n\n\n"
        "def ask(q: str) -> str:\n    return q\n",
        encoding="utf-8",
    )
    return venv_tree(tmp_path)


def _traces(root, payload: str = '{"event": "call"}\n'):
    traces = root / "traces"
    traces.mkdir()
    (traces / "run.jsonl").write_text(payload, encoding="utf-8")
    return traces


def _run(sources, governor, traces_dir=None):
    plugin = load("llm_output_oracle").LLMOutputOraclePlugin()
    context = {"source_dir": str(sources), "cost_governor": governor}
    if traces_dir is not None:
        context["traces_dir"] = str(traces_dir)
    return plugin.run(context)


class TestNothingToAssessIsNotStable:
    def test_absent_traces_report_unknown(self, sources, tmp_path):
        result = _run(sources, StubGovernor("stable"), traces_dir=tmp_path / "nowhere")

        assert result.status == "unknown"
        assert result.findings[0]["stability_assessment"] == "unknown"

    def test_an_empty_traces_directory_reports_unknown(self, sources, tmp_path):
        empty = tmp_path / "traces"
        empty.mkdir()

        result = _run(sources, StubGovernor("stable"), traces_dir=empty)

        assert result.status == "unknown"


class TestTheStatusFollowsTheAssessment:
    def test_variable_output_is_not_a_pass(self, sources, tmp_path):
        result = _run(sources, StubGovernor("variable"), traces_dir=_traces(tmp_path))

        assert result.findings[0]["stability_assessment"] == "variable"
        assert result.status == "warn"

    def test_an_unusable_reply_is_not_a_pass(self, sources, tmp_path):
        result = _run(sources, StubGovernor(""), traces_dir=_traces(tmp_path))

        assert result.findings[0]["stability_assessment"] == "unknown"
        assert result.status == "unknown"


class TestItStillReachesVerdicts:
    """Positive controls: a plugin stuck on "unknown" passes everything above."""

    def test_a_stable_assessment_passes(self, sources, tmp_path):
        result = _run(sources, StubGovernor("Stable\n"), traces_dir=_traces(tmp_path))

        assert result.findings[0]["stability_assessment"] == "stable"
        assert result.status == "pass"

    def test_the_model_was_actually_asked(self, sources, tmp_path):
        governor = StubGovernor("stable")

        _run(sources, governor, traces_dir=_traces(tmp_path))

        assert governor.calls == 1

    def test_the_schema_inventory_still_runs(self, sources, tmp_path):
        result = _run(sources, StubGovernor("stable"), traces_dir=_traces(tmp_path))

        assert result.findings[0]["schema_checks"] == [
            {"file": "agent.py", "return_types": ["str"]}
        ]


class TestItDoesNotScanItsOwnDependencies:
    def test_vendored_code_is_not_reported_as_llm_calling(self, sources, tmp_path):
        result = _run(sources, StubGovernor("stable"), traces_dir=_traces(tmp_path))

        assert result.findings[0]["llm_files"] == ["agent.py"]

    def test_a_tree_with_no_model_calls_is_a_skip(self, tmp_path):
        """Genuinely inapplicable: there is code and none of it calls a model."""
        (tmp_path / "plain.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        plugin = load("llm_output_oracle").LLMOutputOraclePlugin()

        result = plugin.run({"source_dir": str(tmp_path)})

        assert result.status == "skip"
