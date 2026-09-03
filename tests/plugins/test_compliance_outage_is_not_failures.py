"""
An outage is not twenty compliance failures.

``business_rule_compliance`` asked a model about each rule and read ``PASS`` off
the front of the reply. ``complete()`` returns ``""`` when the model was not
reached, ``"".upper().startswith("PASS")`` is False, and so every rule in the
document fell to ``fail`` — and ``COMPLIANCE_WEBHOOK_URL`` fired on exactly that
condition. A five-minute outage posted a real notification naming twenty
compliance failures nobody had observed.

The other direction was worse and quieter: when the client could not be built at
all the handler rewrote every check to ``"unknown"`` and left
``compliance_status`` on the ``"pass"`` it had been initialised with, so the
plugin reported green having asked nothing.

The controls matter more than the failures here. A plugin hardwired to
``unknown`` satisfies every "does not report failures" test in this file while
being worth nothing, so each one is paired with a run that reaches a real
verdict, and one test checks the model was asked at all.
"""

from __future__ import annotations

import requests

from tests.plugins._tier4 import StubGovernor, break_the_model, load

RULES_MD = """# Rules

- A premium must never be negative
- Commission may not exceed one hundred percent
- Every contract carries a start date
"""


def _docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "rules.md").write_text(RULES_MD, encoding="utf-8")
    return docs


def _run(tmp_path, governor):
    plugin = load("business_rule_compliance").BusinessRuleCompliancePlugin()
    return plugin.run({"docs_dir": str(_docs(tmp_path)), "cost_governor": governor})


class _Recorder:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post(self, url, **kwargs):  # noqa: ANN001, ARG002
        self.posts.append(kwargs.get("json", {}))
        return None


def _webhook(monkeypatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setenv("COMPLIANCE_WEBHOOK_URL", "http://compliance.test/hook")
    monkeypatch.setattr(requests, "post", recorder.post)
    return recorder


class TestAnOutageReachesNoVerdict:
    def test_an_empty_reply_is_unknown_not_failure(self, tmp_path):
        result = _run(tmp_path, StubGovernor(""))

        assert result.status == "unknown"
        assert all(c["status"] == "unknown" for c in result.findings[0]["checks"])

    def test_an_error_banner_is_unknown(self, tmp_path):
        result = _run(tmp_path, StubGovernor("Claude API error: 429 rate limited"))

        assert result.status == "unknown"

    def test_an_unreachable_client_is_not_a_pass(self, tmp_path, monkeypatch):
        """The exception handler rewrote the checks and left the verdict alone."""
        break_the_model(monkeypatch)

        result = _run(tmp_path, StubGovernor("TESTABLE assert premium >= 0"))

        assert result.status == "unknown"
        assert result.findings[0]["model_reachable"] is False

    def test_no_webhook_fires_on_an_outage(self, tmp_path, monkeypatch):
        recorder = _webhook(monkeypatch)

        _run(tmp_path, StubGovernor(""))

        assert recorder.posts == []


class TestItStillReachesVerdicts:
    """Positive controls. Every test above is satisfied by a plugin that has
    stopped asking anything at all."""

    def test_a_testable_rule_set_passes(self, tmp_path):
        result = _run(tmp_path, StubGovernor("TESTABLE assert premium >= 0"))

        assert result.status == "pass"
        assert result.findings[0]["testable"] == 3

    def test_an_untestable_rule_fails(self, tmp_path):
        result = _run(tmp_path, StubGovernor("UNTESTABLE this is prose"))

        assert result.status == "fail"
        assert result.findings[0]["untestable"] == 3

    def test_a_real_failure_does_notify(self, tmp_path, monkeypatch):
        """Control for the outage test: the webhook is not simply dead."""
        recorder = _webhook(monkeypatch)

        _run(tmp_path, StubGovernor("UNTESTABLE this is prose"))

        assert len(recorder.posts) == 1
        assert len(recorder.posts[0]["untestable_rules"]) == 3
        assert recorder.posts[0]["rules_without_an_answer"] == 0

    def test_the_model_was_asked_once_per_rule(self, tmp_path):
        governor = StubGovernor("TESTABLE assert premium >= 0")

        _run(tmp_path, governor)

        assert governor.calls == 3


class TestAMissingInputIsNotAnInapplicableOne:
    def test_a_missing_docs_dir_is_unknown(self, tmp_path):
        plugin = load("business_rule_compliance").BusinessRuleCompliancePlugin()

        result = plugin.run({"docs_dir": str(tmp_path / "nowhere")})

        assert result.status == "unknown"

    def test_docs_without_rules_is_unknown(self, tmp_path):
        """SKIP leaves the health score alone, which is the wrong answer when
        the plugin's own premise — that docs/ states the rules — did not hold."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "prose.md").write_text("Just a paragraph.\n", encoding="utf-8")
        plugin = load("business_rule_compliance").BusinessRuleCompliancePlugin()

        result = plugin.run({"docs_dir": str(docs)})

        assert result.status == "unknown"

    def test_a_dry_run_is_scored_neither_way(self, tmp_path):
        plugin = load("business_rule_compliance").BusinessRuleCompliancePlugin()

        result = plugin.dry_run({"docs_dir": str(_docs(tmp_path))})

        assert result.status == "skip"
        assert result.dry_run is True
