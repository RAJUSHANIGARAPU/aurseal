"""
"No regressions" has to mean somebody looked.

``temporal_regression`` initialised ``regression_count = 0`` and raised it only
inside two nested guards: the reply had to be non-empty and not an error banner,
and it had to contain a ``[``. Every other outcome — the empty string of an
outage, a rate-limit banner, a model that answered in prose — fell straight
through to ``"warn" if regression_count > 0 else "pass"``. A replay that
classified nothing was indistinguishable from a replay that came back clean.

It also read up to a hundred events off the log, put ten of them in the prompt,
truncated that at two thousand characters, and reported the verdict against all
hundred.
"""

from __future__ import annotations

import json

import pytest

from tests.plugins._tier4 import StubGovernor, load

CLEAN = json.dumps([{"index": i, "classification": "intentional"} for i in range(3)])
DIRTY = json.dumps(
    [
        {"index": 0, "classification": "intentional"},
        {"index": 1, "classification": "regression"},
    ]
)


@pytest.fixture
def log(tmp_path):
    def _write(count: int = 3):
        path = tmp_path / "events.jsonl"
        path.write_text(
            "\n".join(json.dumps({"id": i, "diff": "x"}) for i in range(count)),
            encoding="utf-8",
        )
        return path

    return _write


def _run(path, governor, monkeypatch):
    monkeypatch.delenv("PROD_EVENT_LOG_PATH", raising=False)
    plugin = load("temporal_regression").TemporalRegressionPlugin()
    return plugin.run({"prod_event_log_path": str(path), "cost_governor": governor})


class TestAnUnusableReplyIsNotACleanReplay:
    @pytest.mark.parametrize(
        "reply",
        ["", "   ", "Claude API error: 429", "I could not find any regressions."],
    )
    def test_it_reports_unknown(self, reply, log, monkeypatch):
        result = _run(log(), StubGovernor(reply), monkeypatch)

        assert result.status == "unknown"

    def test_malformed_json_is_unknown(self, log, monkeypatch):
        result = _run(log(), StubGovernor('[{"index": 0, "classification": '), monkeypatch)

        assert result.status == "unknown"


class TestTheVerdictCoversWhatWasRead:
    def test_events_beyond_the_read_cap_withhold_the_pass(self, log, monkeypatch):
        """Two hundred events in the log, a hundred readable, and the old code
        called the whole file clean."""
        result = _run(log(200), StubGovernor(CLEAN), monkeypatch)

        finding = result.findings[0]
        assert finding["events_in_log"] == 200
        assert finding["events_unexamined"] == 100
        assert result.status == "unknown"


class TestItStillReachesVerdicts:
    """Positive controls: "unknown for everything" passes every test above."""

    def test_a_fully_examined_clean_replay_passes(self, log, monkeypatch):
        result = _run(log(3), StubGovernor(CLEAN), monkeypatch)

        assert result.status == "pass"
        assert result.findings[0]["events_unexamined"] == 0
        assert result.findings[0]["regression_count"] == 0

    def test_a_classified_regression_fails(self, log, monkeypatch):
        result = _run(log(3), StubGovernor(DIRTY), monkeypatch)

        assert result.status == "fail"
        assert result.findings[0]["regression_count"] == 1

    def test_the_model_was_actually_asked(self, log, monkeypatch):
        governor = StubGovernor(CLEAN)

        _run(log(3), governor, monkeypatch)

        assert governor.calls == 1


class TestAMissingLogIsNotAnInapplicableCheck:
    def test_a_named_log_that_is_absent_is_unknown(self, tmp_path, monkeypatch):
        result = _run(tmp_path / "gone.jsonl", StubGovernor(CLEAN), monkeypatch)

        assert result.status == "unknown"

    def test_an_empty_log_is_unknown(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("\n\n", encoding="utf-8")

        result = _run(empty, StubGovernor(CLEAN), monkeypatch)

        assert result.status == "unknown"

    def test_no_log_configured_is_still_a_skip(self, monkeypatch):
        """Control for the two above: nobody asked for a replay, so nobody is
        owed a verdict. This one really is not applicable."""
        monkeypatch.delenv("PROD_EVENT_LOG_PATH", raising=False)
        plugin = load("temporal_regression").TemporalRegressionPlugin()

        assert plugin.run({}).status == "skip"
