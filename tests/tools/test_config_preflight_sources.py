"""Config preflight sources, run offline: every GET and config read comes from fixture files."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from gate.read_only import ReadOnlyClient
from tools.config_preflight import cli
from tools.config_preflight import config_preflight as cp
from tools.config_preflight import sources as src

V1: dict[str, Any] = {
    "key": "subscription",
    "initial_states": ["draft"],
    "states": {
        "draft": {"transitions": [{"action": "submit", "to": "active"}]},
        "active": {
            "transitions": [
                {"action": "cancel", "to": "cancelled"},
                {"action": "pause", "to": "paused"},
            ]
        },
        "paused": {"transitions": [{"action": "resume", "to": "active"}]},
        "cancelled": {"transitions": []},
    },
    "actions": {"submit": {}, "cancel": {}, "pause": {}, "resume": {}},
    "fields": [{"name": "plan_id", "type": "reference", "required": True}],
}


def v2_without_pause() -> dict[str, Any]:
    """The new version: no pausing, so `paused` and the `pause` action are gone."""
    raw = copy.deepcopy(V1)
    del raw["states"]["paused"]
    raw["states"]["active"]["transitions"] = [{"action": "cancel", "to": "cancelled"}]
    del raw["actions"]["pause"], raw["actions"]["resume"]
    return raw


def rec(rid: str, state: str = "active", **extra: Any) -> dict[str, Any]:
    return {"id": rid, "state": state, "fields": {"plan_id": "PLN-1"}, **extra}


class Fixtures:
    """Builds an ``--offline-fixtures`` directory: ``config/<ref>/...`` and ``http/*.json``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._n = itertools.count()
        (root / "http").mkdir(parents=True)

    def config(self, ref: str, path: str, content: Any) -> None:
        file = self.root / "config" / ref / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content if isinstance(content, str) else yaml.safe_dump(content))

    def get(self, path: str, body: Any = None, status: int | str = 200, **kw: Any) -> None:
        entry = {"get": path, "status": status, "body": body, **kw}
        (self.root / "http" / f"{next(self._n):03d}.json").write_text(json.dumps(entry))

    def page(self, path: str, items: list, token: str | None = None, next_token=None, **q):
        query = {"pageSize": q.pop("page_size", 200), **q}
        if token:
            query["nextToken"] = token
        self.get(src.with_query(path, **query), {"items": items, "nextToken": next_token})

    def lists(self, records: list, drafts: list | None = None, kind: str = "subscription"):
        self.page(f"/api/{kind}/records", records)
        self.page(f"/api/{kind}/drafts", drafts or [])


@pytest.fixture
def fx(tmp_path: Path) -> Fixtures:
    f = Fixtures(tmp_path / "fx")
    f.config("v1", "config/subscription.yaml", V1)
    f.config("v2", "config/subscription.yaml", v2_without_pause())
    return f


def run_cli(fx: Fixtures, *extra: str, capsys=None) -> tuple[int, dict[str, Any]]:
    argv = [
        "--offline-fixtures", str(fx.root), "--old-ref", "v1", "--new-ref", "v2",
        "--config-template", "config/{type}.yaml", "--types", "subscription", "--json", *extra,
    ]  # fmt: skip
    code = cli.main(argv)
    body = json.loads(capsys.readouterr().out) if capsys else {}
    return code, body


# ---------------------------------------------------------------- core rule


def test_stale_action_and_stale_route_on_a_draft():
    old = {"subscription": cp.entity_from_mapping(V1)}
    new = {"subscription": cp.entity_from_mapping(v2_without_pause())}
    records = [
        rec("S-1", action="cancel", target="cancelled"),  # still declared
        rec("S-2", action="pause", target="paused"),  # both gone from `active`
        rec("S-3", state="cancelled", target="active"),  # a state with no routes accepts none
    ]
    result = cp.run(old, new, [{**r, "type": "subscription"} for r in records])
    stale = [(f.kind, f.record, f.value) for f in result.findings]
    assert (cp.Finding.STALE_ACTION, "S-2", "pause") in stale
    assert (cp.Finding.STALE_ROUTE, "S-2", "paused") in stale
    [no_routes] = [f for f in result.findings if f.record == "S-3"]
    assert no_routes.kind is cp.Finding.STALE_ROUTE
    assert "has no routes" in no_routes.detail
    assert result.valid == 1


# ---------------------------------------------------------------- scope and config at a ref


def test_manifest_scope_is_case_insensitive_and_types_override_it(fx, capsys):
    fx.config("v2", "deploy/manifest.yaml", {"targets": {"Staging": ["Subscription"]}})
    fx.lists([rec("S-1")])
    argv = [
        "--offline-fixtures", str(fx.root), "--new-ref", "v2",
        "--config-template", "config/{type}.yaml", "--json",
    ]  # fmt: skip
    assert cli.main([*argv, "--manifest", "deploy/manifest.yaml@v2", "--target", "staging"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["scanned"] == {"subscription": {"records": 1, "drafts": 0}}
    # --types wins: the manifest (and its unknown target) is not even read
    assert cli.main([*argv, "--manifest", "deploy/manifest.yaml@v2", "--target", "nope",
                     "--types", "subscription"]) == 0  # fmt: skip
    capsys.readouterr()
    assert cli.main([*argv, "--manifest", "deploy/manifest.yaml@v2", "--target", "nope"]) == 1


def test_templates_are_tried_in_order_and_a_type_without_config_is_undetermined(fx, capsys):
    fx.config("v2", "legacy/subscription.yml", v2_without_pause())
    fx.lists([rec("S-1")])
    argv = [
        "--offline-fixtures", str(fx.root), "--new-ref", "v2", "--json",
        "--config-template", "workflows/{type}.yaml", "--config-template", "legacy/{type}.yml",
        "--types", "subscription,order",
    ]  # fmt: skip
    code = cli.main(argv)
    body = json.loads(capsys.readouterr().out)
    assert code == 1  # `order` has no config at v2: could not determine, never skipped
    [why] = body["undetermined"]
    assert why.startswith("order: could not determine")
    assert body["scanned"] == {"subscription": {"records": 1, "drafts": 0}}


def test_config_from_a_real_git_ref_needs_no_checkout(tmp_path):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("needs the git CLI")
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "subscription.yaml").write_text(yaml.safe_dump(V1))
    git = ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.test",
           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
           "-C", str(repo)]  # fmt: skip
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "v1"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    source = src.GitSource(repo)
    config, missing = src.load_typed_config(source, "HEAD", ["subscription", "order"],
                                            ["config/{type}.yaml"])  # fmt: skip
    assert list(config) == ["subscription"] and missing[0].startswith("order:")
    with pytest.raises(cp.InputError, match="unknown git ref"):
        source.read("no-such-ref", "config/subscription.yaml")


# ---------------------------------------------------------------- paged lists


def test_lists_are_read_to_the_end_and_counted_per_type_and_source(fx, capsys):
    fx.page("/api/subscription/records", [rec("S-1")], next_token="t2")
    fx.page("/api/subscription/records", [rec("S-2")], token="t2", next_token="t3")
    fx.page("/api/subscription/records", [rec("S-3", state="paused")], token="t3")
    fx.page("/api/subscription/drafts", [rec("D-1", action="pause")])
    code, body = run_cli(fx, "--fail-on-reject", capsys=capsys)
    assert code == 2
    assert body["scanned"] == {"subscription": {"records": 3, "drafts": 1}}
    kinds = {(f["record"], f["kind"], f["source"]) for f in body["findings"]}
    assert kinds == {("S-3", "orphaned", "records"), ("D-1", "stale_action", "drafts")}


def test_a_failed_list_makes_the_run_undetermined_not_clean(fx, capsys):
    fx.page("/api/subscription/records", [rec("S-1")], next_token="t2")  # page 2 has no fixture
    fx.page("/api/subscription/drafts", [])
    code, body = run_cli(fx, capsys=capsys)
    assert code == 1
    assert any("subscription records" in u for u in body["undetermined"])


def test_nothing_scanned_is_never_clean(fx, capsys):
    fx.lists([])
    code, body = run_cli(fx, "--fail-on-reject", capsys=capsys)
    assert (code, body["determined"], body["counts"]["records"]) == (1, False, 0)


# ---------------------------------------------------------------- event history


def _events(fx: Fixtures, day: str, nxt: str, items: list, **kw: Any) -> None:
    fx.page("/api/subscription/events", items, page_size=500,
            **{"from": f"{day}T00:00:00Z", "to": f"{nxt}T00:00:00Z"}, **kw)  # fmt: skip


def ev(rid: str, state: str, action: str) -> dict[str, str]:
    return {"recordId": rid, "state": state, "action": action}


def test_event_history_recovers_drafts_hidden_from_the_list(fx, capsys):
    fx.lists([rec("S-1")])
    # day 1: two pages; S-1 already scanned, S-2 declared under v2 -> neither is fetched
    _events(fx, "2026-03-01", "2026-03-02", [ev("S-1", "active", "pause")], next_token="p2")
    _events(fx, "2026-03-01", "2026-03-02",
            [ev("S-2", "active", "cancel"), ev("S-4", "active", "pause")], token="p2")  # fmt: skip
    # day 2: the first page times out once, the retry answers
    window = {"from": "2026-03-02T00:00:00Z", "to": "2026-03-03T00:00:00Z", "pageSize": 500}
    fx.get(src.with_query("/api/subscription/events", **window), sequence=[
        {"status": "timeout"},
        {"status": 200, "body": {"items": [ev("S-5", "paused", "resume")], "nextToken": None}},
    ])  # fmt: skip
    fx.get("/api/subscription/records/S-4", rec("S-4", action="pause"))  # judged as fetched
    fx.get("/api/subscription/records/S-5", status=404)  # gone since

    code, body = run_cli(fx, "--events", "--from", "2026-03-01", "--to", "2026-03-02",
                         "--fail-on-reject", capsys=capsys)  # fmt: skip
    assert code == 2
    assert [(d["day"], d["count"], d["error"]) for d in body["days"]] == [
        ("2026-03-01", 3, None),
        ("2026-03-02", 1, None),
    ]
    assert [(f["record"], f["kind"], f["source"]) for f in body["findings"]] == [
        ("S-4", "stale_action", "events")
    ]
    assert body["gone"] == [{"entity": "subscription", "record": "S-5", "source": "events"}]
    assert body["undetermined"] == []


def test_the_fetched_record_is_judged_not_the_event(fx, capsys):
    fx.lists([])
    _events(fx, "2026-03-01", "2026-03-02", [ev("S-7", "active", "pause")])
    fx.get("/api/subscription/records/S-7", rec("S-7", action="cancel"))  # moved on since
    code, body = run_cli(fx, "--events", "--from", "2026-03-01", "--to", "2026-03-01",
                         "--fail-on-reject", capsys=capsys)  # fmt: skip
    assert (code, body["findings"], body["counts"]["valid"]) == (0, [], 1)


def test_a_failed_day_is_recorded_while_the_other_days_continue(fx, capsys):
    fx.lists([rec("S-1")])
    _events(fx, "2026-03-01", "2026-03-02", [])
    window = {"from": "2026-03-02T00:00:00Z", "to": "2026-03-03T00:00:00Z", "pageSize": 500}
    fx.get(src.with_query("/api/subscription/events", **window), status="timeout")
    _events(fx, "2026-03-03", "2026-03-04", [])
    code, body = run_cli(fx, "--events", "--from", "2026-03-01", "--to", "2026-03-03",
                         "--workers", "3", capsys=capsys)  # fmt: skip
    assert code == 1
    errors = {d["day"]: d["error"] for d in body["days"]}
    assert errors["2026-03-01"] is None and errors["2026-03-03"] is None
    assert "timed out" in errors["2026-03-02"]
    assert body["undetermined"] == ["subscription events: 1 of 3 day(s) failed"]


def test_events_403_is_not_authorised_and_the_run_continues(fx, capsys):
    fx.lists([rec("S-1", state="paused")])
    window = {"from": "2026-03-01T00:00:00Z", "to": "2026-03-02T00:00:00Z", "pageSize": 500}
    fx.get(src.with_query("/api/subscription/events", **window), status=403)
    code, body = run_cli(fx, "--events", "--from", "2026-03-01", "--to", "2026-03-01",
                         capsys=capsys)  # fmt: skip
    assert code == 1
    assert body["days"][0]["error"] == "not authorised (403)"
    assert body["undetermined"] == ["subscription events: 1 of 1 day(s) not authorised"]
    assert body["counts"]["orphaned"] == 1  # the lists were still judged


# ---------------------------------------------------------------- ids, migration hint


def test_ids_file_fetches_each_record_and_404_is_gone(fx, tmp_path, capsys):
    fx.lists([])
    fx.get("/api/subscription/records/S-8", rec("S-8", state="paused"))
    fx.get("/api/subscription/records/S-9", status=404)
    ids = tmp_path / "ids.txt"
    ids.write_text("# picked by hand\nS-8\nsubscription S-9\n")
    code, body = run_cli(fx, "--ids", str(ids), "--fail-on-reject", capsys=capsys)
    assert code == 2
    assert [(f["record"], f["source"]) for f in body["findings"]] == [("S-8", "ids")]
    assert body["gone"] == [{"entity": "subscription", "record": "S-9", "source": "ids"}]


@pytest.mark.parametrize(
    ("script", "verdict"),
    [
        (
            "UPDATE subscription SET state = 'active' WHERE state = 'paused';",
            "covered-by-migration?",
        ),
        ("-- paused_legacy is handled elsewhere", "finding"),  # not a whole word
    ],
)
def test_migration_script_hint(fx, capsys, script, verdict):
    fx.config("v2", "migrations/subscription.sql", script)
    fx.lists([rec("S-1", state="paused")])
    code, body = run_cli(fx, "--migration-template", "migrations/{type}.sql",
                         "--fail-on-reject", capsys=capsys)  # fmt: skip
    assert code == 2  # a hint is still a finding
    [finding] = body["findings"]
    assert finding["verdict"] == verdict


# ---------------------------------------------------------------- plumbing


def test_fixture_http_refuses_an_unanswered_get(fx):
    client = ReadOnlyClient(src.FixtureHttp(fx.root).get)
    with pytest.raises(src.SourceError, match="no fixture"):
        src.get_json(client, "/api/subscription/records?pageSize=200")


def test_http_record_reader_follows_next_tokens(fx):
    fx.page("/api/subscription/records", [rec("S-1")], next_token="t2")
    fx.page("/api/subscription/records", [rec("S-2")], token="t2")
    client = ReadOnlyClient(src.FixtureHttp(fx.root).get)
    reader = src.HttpRecordReader(client, "/api/subscription/records")
    assert [r["id"] for r in reader.records()] == ["S-1", "S-2"]


def test_token_from_file_or_env(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("abc\n")
    monkeypatch.setenv("PREFLIGHT_TEST_TOKEN", "from-env")
    assert src.read_token("PREFLIGHT_TEST_TOKEN", str(token)) == "abc"
    assert src.read_token("PREFLIGHT_TEST_TOKEN", None) == "from-env"
    token.write_text("")
    with pytest.raises(cp.InputError, match="empty"):
        src.read_token(None, str(token))


def test_markdown_summary_lists_scanned_counts_per_type_and_source(fx, capsys):
    fx.lists([rec("S-1")], [rec("D-1", action="cancel")])
    argv = ["--offline-fixtures", str(fx.root), "--old-ref", "v1", "--new-ref", "v2",
            "--config-template", "config/{type}.yaml", "--types", "subscription"]  # fmt: skip
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "| Entity | drafts | records |" in out
    assert "| subscription | 1 | 1 |" in out
