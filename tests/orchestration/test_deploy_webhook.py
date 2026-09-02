"""
What ``--dry-run`` and a failing webhook do to the deploy notification.

``MasterOrchestrator.run`` posted to ``DEPLOY_WEBHOOK_URL`` unconditionally: it
never looked at ``dry_run``, so the command the README documents as having no
side effects —

    python -m orchestration.master_orchestrator --trigger deploy --dry-run

— sent a real HTTP POST to whatever the deploy webhook points at. The same
block ended in ``except Exception: pass``, so a webhook that was down, wrong or
rejecting the payload produced no trace anywhere and the run reported success.

The orchestrator is built here with ``object.__new__`` and fake collaborators:
``__init__`` scans the real plugin tree, opens SQLite and starts a trace file,
and an empty registry is what makes the run fast while still exercising the
real ``run()`` path — including the argument plumbing that carries ``dry_run``
from the CLI to the webhook block.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from orchestration.master_orchestrator import MasterOrchestrator

WEBHOOK = "https://deploy.example.com/hook"


class FakeLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def log(self, agent: str, event_type: str, data: dict | None = None) -> None:
        self.events.append((agent, event_type, data or {}))

    def events_named(self, needle: str) -> list[tuple[str, str, dict]]:
        return [e for e in self.events if needle in e[1]]


class FakeRegistry:
    def get_by_trigger(self, event: str) -> list:
        return []

    def all(self) -> list:
        return []


class FakeStorage:
    def __init__(self) -> None:
        self.runs: list[tuple] = []

    def save_run(self, *args) -> None:
        self.runs.append(args)

    def save_plugin_result(self, *args) -> None:
        pass


@dataclass
class FakeResponse:
    status_code: int = 200


@dataclass
class PostRecorder:
    """Stands in for ``requests.post``."""

    calls: list[dict] = field(default_factory=list)
    raises: Exception | None = None
    response: FakeResponse = field(default_factory=FakeResponse)

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.raises is not None:
            raise self.raises
        return self.response


@pytest.fixture
def orchestrator() -> MasterOrchestrator:
    instance = object.__new__(MasterOrchestrator)
    instance._governor = None
    instance._registry = FakeRegistry()
    instance._storage = FakeStorage()
    instance._logger = FakeLogger()
    return instance


@pytest.fixture
def post(monkeypatch) -> PostRecorder:
    recorder = PostRecorder()
    monkeypatch.setattr("requests.post", recorder)
    return recorder


class TestDryRunSendsNothing:
    def test_a_dry_run_does_not_post(self, orchestrator, post, monkeypatch):
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        orchestrator.run({"trigger": "deploy", "dry_run": True})
        assert post.calls == []

    def test_a_dry_run_says_why_it_sent_nothing(self, orchestrator, post, monkeypatch):
        """Silence is what the old bug looked like too — the skip has to be visible."""
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        orchestrator.run({"trigger": "deploy", "dry_run": True})
        skipped = orchestrator._logger.events_named("webhook_skipped")
        assert len(skipped) == 1

    def test_no_webhook_configured_posts_nothing(self, orchestrator, post, monkeypatch):
        monkeypatch.delenv("DEPLOY_WEBHOOK_URL", raising=False)
        orchestrator.run({"trigger": "deploy", "dry_run": False})
        assert post.calls == []


class TestARealRunStillNotifies:
    """
    Positive controls.

    Every test above asserts that nothing was sent. Deleting the webhook call
    outright would satisfy all of them, so these prove the notification still
    works when the run is real.
    """

    def test_a_real_run_posts_once(self, orchestrator, post, monkeypatch):
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        orchestrator.run({"trigger": "deploy", "dry_run": False})
        assert len(post.calls) == 1
        assert post.calls[0]["url"] == WEBHOOK

    def test_a_run_with_no_dry_run_flag_at_all_posts(self, orchestrator, post, monkeypatch):
        """The library entry point passes no such key — that is not a dry run."""
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        orchestrator.run({"trigger": "manual"})
        assert len(post.calls) == 1

    def test_the_payload_still_carries_the_verdict(self, orchestrator, post, monkeypatch):
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        result = orchestrator.run({"trigger": "deploy", "dry_run": False})
        payload = post.calls[0]["json"]
        assert payload["run_id"] == result["run_id"]
        assert payload["health_score"] == result["health_score"]
        assert payload["deploy"] == result["deploy"]


class TestDeliveryFailuresAreLogged:
    def test_a_connection_error_is_logged(self, orchestrator, post, monkeypatch):
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        post.raises = ConnectionError("name or service not known")
        orchestrator.run({"trigger": "deploy", "dry_run": False})

        errors = orchestrator._logger.events_named("webhook_error")
        assert len(errors) == 1
        assert "name or service not known" in errors[0][2]["error"]

    def test_a_connection_error_does_not_break_the_run(self, orchestrator, post, monkeypatch):
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        post.raises = ConnectionError("boom")
        result = orchestrator.run({"trigger": "deploy", "dry_run": False})
        assert "health_score" in result

    def test_a_rejected_payload_is_logged(self, orchestrator, post, monkeypatch):
        """A 500 is a delivery failure too — it just does not raise."""
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        post.response = FakeResponse(status_code=500)
        orchestrator.run({"trigger": "deploy", "dry_run": False})

        failures = orchestrator._logger.events_named("webhook_failed")
        assert len(failures) == 1
        assert failures[0][2]["status_code"] == 500

    def test_a_delivered_webhook_logs_no_failure(self, orchestrator, post, monkeypatch):
        """Positive control for the two tests above."""
        monkeypatch.setenv("DEPLOY_WEBHOOK_URL", WEBHOOK)
        orchestrator.run({"trigger": "deploy", "dry_run": False})
        assert orchestrator._logger.events_named("webhook_error") == []
        assert orchestrator._logger.events_named("webhook_failed") == []
