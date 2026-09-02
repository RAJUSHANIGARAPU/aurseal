"""
An unreachable model must not produce a confident answer downstream.

``behavioral_equivalence`` asked Claude whether a refactor's added/removed
functions were "safe" or "semantic", then wrote
``classification if classification in ("safe","semantic") else "semantic"``. Any
unrecognised string — including the empty string ``base.complete()`` documents
as failure — became "semantic", i.e. "this refactor changed behaviour". An
outage therefore reported drift on every function that had moved.

``CostGovernor.cached_complete`` then stored that by prompt hash, so one 429
early in a run was replayed to every later caller of the same prompt without a
call being made: the run could not recover after the throttle lifted.

The controls below matter more than usual here. "Never says semantic" is
satisfied by a plugin that has stopped classifying, and "does not cache" is
satisfied by a cache that stores nothing — so each half asserts the healthy path
too.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from plugins.cost_governor import CostGovernor

_PLUGIN = (
    Path(__file__).resolve().parents[2] / "plugins" / "tier4" / "behavioral_equivalence.plugin.py"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("behavioral_equivalence_under_test", _PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BehavioralEquivalencePlugin()


class _StubGovernor(CostGovernor):
    """A governor whose model call is canned — nothing here reaches Anthropic."""

    def __init__(self, reply: str) -> None:
        super().__init__()
        self._reply = reply
        self.calls = 0

    def cached_complete(self, prompt, call_fn):  # noqa: ARG002
        self.calls += 1
        return self._reply


@pytest.fixture
def refactored_source(tmp_path, monkeypatch):
    """A tree whose snapshot has drifted, so the plugin has to classify.

    ``_SNAPSHOT_DIR`` is cwd-relative, so chdir keeps the snapshot out of the
    repo's own ``reports/``.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mod.py").write_text("def kept():\n    pass\n\ndef added():\n    pass\n")
    return tmp_path


def _classifications(plugin, source_dir, governor):
    """Run pre_refactor to lay a snapshot down, then post_refactor to diff it."""
    plugin.run({"source_dir": str(source_dir), "trigger": "pre_refactor"})
    (source_dir / "mod.py").write_text("def kept():\n    pass\n\ndef renamed():\n    pass\n")
    result = plugin.run(
        {"source_dir": str(source_dir), "trigger": "post_refactor", "cost_governor": governor}
    )
    return result, result.findings[0]["drift_classifications"]


class TestAnOutageIsNotADriftVerdict:
    @pytest.mark.parametrize("reply", ["", "   ", "claude CLI timeout (120s)", "I'm sorry, but"])
    def test_an_unusable_reply_classifies_as_unknown(self, reply, refactored_source):
        plugin = _load_plugin()
        _, classifications = _classifications(plugin, refactored_source, _StubGovernor(reply))

        assert classifications, "the drift was not detected at all — the fixture is wrong"
        assert all(c["classification"] == "unknown" for c in classifications)

    def test_an_unclassified_drift_does_not_report_pass(self, refactored_source):
        """
        Calling it "pass" because the model was unreachable is the same false
        green as calling it "semantic", pointed the other way.
        """
        plugin = _load_plugin()
        result, _ = _classifications(plugin, refactored_source, _StubGovernor(""))
        assert result.status == "warn"


class TestTheClassifierStillClassifies:
    """Positive control: a plugin that answered "unknown" to everything would
    satisfy every test above while being useless."""

    def test_a_safe_verdict_is_kept(self, refactored_source):
        plugin = _load_plugin()
        result, classifications = _classifications(plugin, refactored_source, _StubGovernor("safe"))
        assert [c["classification"] for c in classifications] == ["safe"]
        assert result.status == "pass"

    def test_a_semantic_verdict_is_kept(self, refactored_source):
        plugin = _load_plugin()
        result, classifications = _classifications(
            plugin, refactored_source, _StubGovernor("Semantic\n")
        )
        assert [c["classification"] for c in classifications] == ["semantic"]
        assert result.status == "warn"

    def test_the_model_was_actually_asked(self, refactored_source):
        """If the call stopped happening, "unknown" would be right for the wrong
        reason and nothing above would notice."""
        plugin = _load_plugin()
        governor = _StubGovernor("safe")
        _classifications(plugin, refactored_source, governor)
        assert governor.calls == 1


class TestTheCacheDoesNotMemoiseAnOutage:
    def test_an_empty_reply_is_not_cached(self):
        gov = CostGovernor()
        calls = []

        def flaky(prompt: str) -> str:
            calls.append(prompt)
            return "" if len(calls) == 1 else "safe"

        assert gov.cached_complete("p", flaky) == ""
        assert gov.cached_complete("p", flaky) == "safe", "the outage was replayed from cache"
        assert len(calls) == 2

    def test_a_recovered_answer_is_then_cached(self):
        """The retry must still be a one-off: once real content arrives it
        caches, or a persistent outage becomes an unbounded call loop."""
        gov = CostGovernor()
        calls = []

        def flaky(prompt: str) -> str:
            calls.append(prompt)
            return "" if len(calls) == 1 else "safe"

        gov.cached_complete("p", flaky)
        gov.cached_complete("p", flaky)
        gov.cached_complete("p", flaky)
        assert len(calls) == 2

    def test_a_successful_reply_is_still_cached(self):
        """Positive control: a cache that stored nothing passes the first test."""
        gov = CostGovernor()
        calls = []

        def once(prompt: str) -> str:
            calls.append(prompt)
            return "safe"

        assert gov.cached_complete("p", once) == "safe"
        assert gov.cached_complete("p", once) == "safe"
        assert len(calls) == 1


class TestTheFlakinessClassifierAlreadyHandledThis:
    """
    ``pattern_analyzer`` was already correct — ``FlakPattern(raw)`` raises on
    anything it does not know and falls to UNKNOWN. Pinning it so the fix above
    does not later get "harmonised" into the broken shape.
    """

    @pytest.mark.parametrize("reply", ["", "claude CLI error: exit 1", "nonsense"])
    def test_an_unusable_reply_is_unknown(self, reply):
        from autonomous_ui.flakiness.models import FlakPattern
        from autonomous_ui.flakiness.pattern_analyzer import PatternAnalyzer

        class _Stub:
            def complete(self, prompt: str, max_tokens: int = 2048) -> str:  # noqa: ARG002
                return reply

        analyzer = PatternAnalyzer(llm=_Stub())
        assert analyzer._llm_classify(_profile(), []) is FlakPattern.UNKNOWN

    def test_a_real_classification_survives(self):
        """Control for the parametrised test above."""
        from autonomous_ui.flakiness.models import FlakPattern
        from autonomous_ui.flakiness.pattern_analyzer import PatternAnalyzer

        class _Stub:
            def complete(self, prompt: str, max_tokens: int = 2048) -> str:  # noqa: ARG002
                return "timing\n"

        analyzer = PatternAnalyzer(llm=_Stub())
        assert analyzer._llm_classify(_profile(), []) is FlakPattern.TIMING


def _profile():
    from autonomous_ui.flakiness.models import FlakinessProfile

    return FlakinessProfile(
        test_id="tests/test_thing.py::test_x",
        total_runs=10,
        failure_count=3,
        flakiness_rate=0.3,
        confidence=1.0,
        is_flaky=True,
        most_common_error="TimeoutError",
        avg_duration_s=1.5,
        last_failure_ts="",
        max_consecutive_failures=2,
    )
