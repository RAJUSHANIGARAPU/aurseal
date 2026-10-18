"""Scheduling: declared crons, run trigger, staleness, and the dispatch adapter."""

from __future__ import annotations

import io
import json
import textwrap
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from gate.ci_provider import DispatchError, GitHubActionsProvider, normalise_inputs
from gate.schedules import (
    HEALTHY,
    STALE,
    UNKNOWN,
    StoreScheduleHistory,
    load_schedules,
    main,
    schedule_health,
    schedule_name_from_env,
    trigger_from_env,
)

NOW = datetime(2030, 1, 10, 12, 0, tzinfo=timezone.utc)
MAX_AGE = timedelta(hours=36)

SCHEDULED = textwrap.dedent(
    """\
    name: Tests
    on:
      push:
        branches: [main]
      schedule:
        - cron: "17 2 * * *"
        - cron: "40 5 * * 0"
      workflow_dispatch:
        inputs:
          lanes:
            type: string
    jobs:
      offline:
        runs-on: ubuntu-latest
        steps:
          - run: pytest tests/
      nightly:
        if: github.event_name == 'schedule'
        runs-on: ubuntu-latest
        steps:
          - run: pytest -m slow
      browser:
        runs-on: ubuntu-latest
        steps:
          - run: pytest -m smoke
          - if: github.event_name == 'schedule' || contains(inputs.lanes, 'regression')
            run: pytest -m regression
    """
)


def _write(tmp_path, text, name="wf.yml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class FakeHistory:
    def __init__(self, last=None, error=None):
        self.last, self.error, self.asked = last, error, []

    def last_scheduled_run(self, schedule_name=None):
        self.asked.append(schedule_name)
        if self.error:
            raise self.error
        return self.last


class TestLoadSchedules:
    def test_workflow_without_schedule_returns_nothing(self, tmp_path):
        path = _write(tmp_path, "name: X\non: [push]\njobs: {}\n")
        assert load_schedules(path) == []

    def test_reads_every_cron_and_the_jobs_that_opt_in(self, tmp_path):
        specs = load_schedules(_write(tmp_path, SCHEDULED))
        assert [s.cron for s in specs] == ["17 2 * * *", "40 5 * * 0"]
        assert specs[0].name == "Tests:17 2 * * *"
        assert specs[0].lanes == ("nightly", "browser")

    def test_on_key_read_as_boolean_by_yaml_is_handled(self, tmp_path):
        # `on:` unquoted is YAML 1.1 True; the parser must still find the schedule.
        assert load_schedules(_write(tmp_path, SCHEDULED))

    def test_empty_file(self, tmp_path):
        assert load_schedules(_write(tmp_path, "")) == []


class TestTrigger:
    @pytest.mark.parametrize(
        "event,expected",
        [
            ("push", "push"),
            ("pull_request", "pull_request"),
            ("schedule", "schedule"),
            ("workflow_dispatch", "dispatch"),
            ("repository_dispatch", "api"),
            ("release", "manual"),
        ],
    )
    def test_ci_event_maps_to_trigger(self, event, expected):
        assert trigger_from_env({"GITHUB_EVENT_NAME": event}) == expected

    def test_outside_ci_is_manual(self):
        assert trigger_from_env({}) == "manual"

    def test_explicit_override_wins_and_is_validated(self):
        assert trigger_from_env({"RUN_TRIGGER": "api", "GITHUB_EVENT_NAME": "push"}) == "api"
        with pytest.raises(ValueError):
            trigger_from_env({"RUN_TRIGGER": "nightly"})

    def test_schedule_name_comes_from_the_event_payload(self, tmp_path):
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"schedule": "17 2 * * *"}), encoding="utf-8")
        env = {
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_WORKFLOW": "Tests",
        }
        assert schedule_name_from_env(env) == "Tests:17 2 * * *"
        assert schedule_name_from_env({"GITHUB_EVENT_NAME": "push"}) is None


class TestScheduleHealth:
    def test_recent_run_is_healthy(self):
        health = schedule_health(FakeHistory(NOW - timedelta(hours=2)), NOW, MAX_AGE, "n")
        assert health.status == HEALTHY

    def test_old_run_is_stale(self):
        health = schedule_health(FakeHistory(NOW - timedelta(hours=50)), NOW, MAX_AGE, "n")
        assert health.status == STALE
        assert "exceeds" in health.reason

    def test_never_fired_is_stale_not_healthy(self):
        assert schedule_health(FakeHistory(None), NOW, MAX_AGE).status == STALE

    def test_no_history_is_unknown(self):
        assert schedule_health(None, NOW, MAX_AGE).status == UNKNOWN

    def test_unreadable_history_is_unknown(self):
        health = schedule_health(FakeHistory(error=OSError("locked")), NOW, MAX_AGE)
        assert health.status == UNKNOWN
        assert "locked" in health.reason

    def test_naive_timestamps_are_read_as_utc(self):
        last = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        assert schedule_health(FakeHistory(last), NOW, MAX_AGE).status == HEALTHY


class TestStoreAdapter:
    def test_reads_newest_scheduled_run_through_the_named_query(self):
        calls = []

        class Store:
            def query(self, name, **params):
                calls.append((name, params))
                return [{"started_at": "2030-01-10T10:00:00Z"}]

        last = StoreScheduleHistory(Store()).last_scheduled_run("Tests:17 2 * * *")
        assert last == datetime(2030, 1, 10, 10, 0, tzinfo=timezone.utc)
        assert calls == [
            (
                "last_run_by_trigger",
                {"trigger": "schedule", "schedule_name": "Tests:17 2 * * *"},
            )
        ]

    def test_no_rows_is_never_fired(self):
        class Store:
            def query(self, name, **params):
                return []

        assert StoreScheduleHistory(Store()).last_scheduled_run() is None


class TestCli:
    def test_no_schedule_exits_3(self, tmp_path):
        out = io.StringIO()
        path = _write(tmp_path, "name: X\non: [push]\njobs: {}\n")
        assert main(["--check", "--workflow", str(path)], out=out) == 3

    def test_stale_exits_1(self, tmp_path):
        out = io.StringIO()
        path = _write(tmp_path, SCHEDULED)
        code = main(["--check", "--workflow", str(path)], out=out, history=FakeHistory(None))
        assert code == 1
        assert "STALE" in out.getvalue()

    def test_healthy_exits_0(self, tmp_path):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        path = _write(tmp_path, SCHEDULED)
        out = io.StringIO()
        assert main(["--check", "--workflow", str(path)], out=out, history=FakeHistory(recent)) == 0

    def test_unknown_history_exits_2(self, tmp_path):
        path = _write(tmp_path, SCHEDULED)
        history = FakeHistory(error=RuntimeError("no store"))
        assert main(["--check", "--workflow", str(path)], out=io.StringIO(), history=history) == 2


class FakeResponse:
    def __init__(self, status):
        self.status = status


class TestGitHubProvider:
    def test_builds_documented_url_and_body(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
        seen = []

        def opener(request):
            seen.append(request)
            return FakeResponse(204)

        provider = GitHubActionsProvider("owner/repo", opener=opener, allowed_lanes=["slow"])
        dispatch_id = provider.dispatch(
            "tests.yml", "main", {"lanes": ["slow"], "browser_matrix": True, "env": "qa"}
        )
        request = seen[0]
        assert request.full_url == (
            "https://api.github.com/repos/owner/repo/actions/workflows/tests.yml/dispatches"
        )
        assert request.get_method() == "POST"
        assert request.headers["Authorization"] == "Bearer t0ken"
        assert json.loads(request.data) == {
            "ref": "main",
            "inputs": {"lanes": "slow", "browser_matrix": "true", "env": "qa"},
        }
        assert dispatch_id.startswith("tests.yml@main:")

    def test_non_204_raises(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")
        provider = GitHubActionsProvider("o/r", opener=lambda r: FakeResponse(200))
        with pytest.raises(DispatchError, match="204"):
            provider.dispatch("tests.yml", "main", {})

    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "t")

        def opener(request):
            raise urllib.error.HTTPError(request.full_url, 422, "bad", {}, None)

        with pytest.raises(DispatchError, match="422"):
            GitHubActionsProvider("o/r", opener=opener).dispatch("tests.yml", "main", {})

    def test_missing_token_refuses_before_sending(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        sent = []
        provider = GitHubActionsProvider("o/r", opener=sent.append)
        with pytest.raises(DispatchError, match="GITHUB_TOKEN"):
            provider.dispatch("tests.yml", "main", {})
        assert sent == []

    def test_bad_repo_rejected(self):
        with pytest.raises(ValueError):
            GitHubActionsProvider("repo-only")


class TestNormaliseInputs:
    def test_unknown_lane_refused(self):
        with pytest.raises(ValueError, match="unknown lane"):
            normalise_inputs({"lanes": "slow,typo"}, allowed_lanes=["slow"])

    def test_comma_string_and_none_dropped(self):
        assert normalise_inputs({"lanes": " slow , regression ", "env": None}) == {
            "lanes": "slow,regression"
        }


def test_repository_workflow_declares_a_nightly():
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"
    assert load_schedules(workflow), "the nightly schedule was removed from tests.yml"


def test_store_history_reads_the_newest_scheduled_run_from_the_results_store():
    from results.models import RunRecord
    from results.store import open_store

    store = open_store(":memory:")
    for run_id, trigger, started in (
        ("r1", "schedule", "2026-10-01T02:17:00+00:00"),
        ("r2", "schedule", "2026-10-02T02:17:00+00:00"),
        ("r3", "push", "2026-10-03T09:00:00+00:00"),
    ):
        store.begin_run(
            RunRecord(
                run_id=run_id, trigger=trigger, schedule_name="Tests:17 2 * * *", started_at=started
            )
        )
    history = StoreScheduleHistory(store)
    assert history.last_scheduled_run() == datetime(2026, 10, 2, 2, 17, tzinfo=timezone.utc)
    assert history.last_scheduled_run("Tests:other") is None
