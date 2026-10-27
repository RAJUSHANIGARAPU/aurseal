"""Release lead time on temporary git repositories with fixed commit and tag dates."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.tools.test_promotion_diff import write
from tools.release_leadtime import release_leadtime as rl

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")


def git(repo: Path, *args: str, at: str = "2026-01-01T00:00:00Z") -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Dev",
            "-c",
            "user.email=dev@example.test",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()


def commit(repo: Path, subject: str, at: str, name: str, content: str | None = None) -> None:
    (repo / name).write_text(content if content is not None else subject + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject, at=at)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """``orders`` mainline, January 2026 (UTC):

    01-02T10 merge BUG-1, 01-03T10 ORD-2, tag v1.0 01-04T10, 01-05T10 ORD-3,
    tag v1.1 01-06T10, hotfix BUG-9 on a branch off v1.1 tagged v1.1-hotfix-1
    01-06T13, 01-07T00 ORD-4 under the LIGHT tag v1.2, 01-08 untagged work.
    """
    ws = tmp_path / "ws"
    orders = ws / "orders"
    orders.mkdir(parents=True)
    git(orders, "init", "-q", "-b", "main")
    commit(orders, "initial", "2026-01-01T00:00:00Z", "a.txt")
    git(orders, "checkout", "-q", "-b", "feature")
    commit(orders, "BUG-1 work in progress", "2026-01-01T12:00:00Z", "b.txt")
    git(orders, "checkout", "-q", "main")
    git(orders, "merge", "-q", "--no-ff", "-m", "Merge BUG-1 fix rounding", "feature",
        at="2026-01-02T10:00:00Z")  # fmt: skip
    commit(orders, "ORD-2 squash of a branch", "2026-01-03T10:00:00Z", "c.txt")
    git(orders, "tag", "-a", "v1.0", "-m", "v1.0", at="2026-01-04T10:00:00Z")
    commit(orders, "ORD-3 later change", "2026-01-05T10:00:00Z", "d.txt")
    git(orders, "tag", "-a", "v1.1", "-m", "v1.1", at="2026-01-06T10:00:00Z")
    git(orders, "checkout", "-q", "-b", "hotfix")
    commit(orders, "BUG-9 hotfix", "2026-01-06T12:00:00Z", "h.txt")
    git(orders, "tag", "-a", "v1.1-hotfix-1", "-m", "hf", at="2026-01-06T13:00:00Z")
    git(orders, "checkout", "-q", "main")
    commit(orders, "ORD-4 next change", "2026-01-07T00:00:00Z", "e.txt")
    git(orders, "tag", "v1.2")  # light tag: no tag date of its own
    git(orders, "tag", "build-7")  # not a release tag under the default glob
    commit(orders, "untagged work", "2026-01-08T00:00:00Z", "f.txt")

    billing = ws / "billing-service"
    billing.mkdir()
    git(billing, "init", "-q", "-b", "main")
    commit(billing, "BIL-9 first", "2026-01-03T00:00:00Z", "a.txt")
    git(billing, "tag", "-a", "v1.0", "-m", "v1.0", at="2026-01-03T06:00:00Z")
    return ws


@pytest.fixture
def deployments(tmp_path: Path) -> Path:
    log = tmp_path / "deployments.csv"
    log.write_text(
        "service,tag,environment,deployed_at\n"
        "orders,v1.0,test,2026-01-04T12:00:00Z\n"
        "orders,v1.0,acceptance,2026-01-05T00:00:00Z\n"
        "orders,v1.0,production,2026-01-05T10:00:00Z\n"
        "orders,v1.0,production-dr,2026-01-05T12:00:00Z\n"
        "orders,v1.0,production,2026-01-06T10:00:00Z\n"  # a redeploy: the first one counts
        "orders,v1.0,acceptance,2026-01-07T00:00:00Z\n"  # a rollback re-pin: ignored
        "orders,v1.1,test,2026-01-06T11:00:00Z\n"
        "orders,v1.1,acceptance,2026-01-06T12:00:00Z\n"
        "orders,v1.1-hotfix-1,test,2026-01-06T14:00:00Z\n"
        "orders,v1.1-hotfix-1,production,2026-01-07T00:00:00Z\n"  # skipped acceptance
        "orders,v1.2,test,2026-01-07T06:00:00Z\n"
        "orders,v1.2,acceptance,2026-01-08T00:00:00Z\n"
        "orders,v1.2,production-dr,2026-01-08T20:00:00Z\n"  # earliest production env wins
        "orders,v1.2,production,2026-01-09T00:00:00\n"  # no offset: UTC
        "orders,v1.2,sandbox,2026-01-07T01:00:00Z\n"  # not in the chain: ignored
    )
    return log


ENVS = ["--envs", "test,acceptance,production", "--production", "production,production-dr"]


def run(workspace: Path, *extra: str) -> int:
    return rl.main(
        [
            "--workspace",
            str(workspace),
            "--service",
            "orders",
            "--service",
            "billing=billing-service",
            *extra,
        ]
    )


def as_json(workspace: Path, capsys, *extra: str) -> dict:
    assert run(workspace, "--json", *extra) == 0
    return json.loads(capsys.readouterr().out)


def service(data: dict, name: str) -> dict:
    return next(s for s in data["changes"]["services"] if s["service"] == name)


def group(data: dict, scope: str, kind: str) -> dict:
    return next(g for g in data["releases"]["groups"] if (g["scope"], g["kind"]) == (scope, kind))


def measured(n, pending, median, p90, six, day) -> dict:
    return {
        "status": "measured",
        "n": n,
        "pending": pending,
        "median_hours": median,
        "p90_hours": p90,
        "at_or_under": {"6h": six, "24h": day},
    }


def test_change_stages(workspace, deployments, capsys):
    data = as_json(
        workspace, capsys, "--deployments", str(deployments), "--since", "2026-01-02", *ENVS
    )
    orders = service(data, "orders")
    assert orders["changes"] == 5 and orders["untagged"] == 2  # ORD-4 is under a light tag only
    assert orders["stages"]["merge_to_tag"] == measured(3, 2, 24.0, 48.0, 0, 2)
    # merge BUG-1 72h, ORD-2 48h, ORD-3 38h (via the hotfix tag), ORD-4 44h (production-dr)
    assert orders["stages"]["merge_to_prod"] == measured(4, 1, 46.0, 72.0, 0, 0)
    slowest = data["changes"]["slowest"]
    assert [r["subject"] for r in slowest[:2]] == [
        "Merge BUG-1 fix rounding",
        "ORD-2 squash of a branch",
    ]
    third = next(r for r in slowest if r["subject"] == "ORD-3 later change")
    assert third["tag"] == "v1.1" and third["deployed_tag"] == "v1.1-hotfix-1"
    assert third["keys"] == ["ORD-3"] and third["ranked_by"] == "merge_to_prod"


def test_release_stages_per_consecutive_env_pair_and_tag_to_production(
    workspace, deployments, capsys
):
    data = as_json(workspace, capsys, "--deployments", str(deployments), *ENVS)
    assert data["chain"] == ["test", "acceptance", "production"]
    regular = group(data, "orders", "regular")
    assert regular["units"] == 3  # v1.0, v1.1, v1.2: each (service, tag) once
    assert regular["stages"] == {
        "test -> acceptance": measured(3, 0, 12.0, 18.0, 1, 3),
        "acceptance -> production": measured(2, 1, 15.0, 20.0, 0, 2),
        "tag -> production": measured(1, 1, 24.0, 24.0, 0, 1),  # v1.2 is light: left out
    }
    hotfix = group(data, "orders", "hotfix")
    assert hotfix["units"] == 1
    assert hotfix["stages"]["test -> acceptance"] == {"status": "no data", "n": 0, "pending": 1}
    assert hotfix["stages"]["tag -> production"] == measured(1, 0, 11.0, 11.0, 0, 1)
    assert data["releases"]["lightweight_tags"] == ["orders v1.2"]
    units = {u["tag"]: u for u in data["releases"]["units"]}
    assert units["v1.0"]["reached"]["production"] == "2026-01-05T10:00:00+00:00"
    assert units["v1.0"]["reached"]["acceptance"] == "2026-01-05T00:00:00+00:00"
    assert units["v1.2"]["reached"]["production"] == "2026-01-08T20:00:00+00:00"
    assert units["v1.2"]["tagged_at"] is None and units["v1.1-hotfix-1"]["kind"] == "hotfix"


def test_service_without_deployment_data_is_not_measured_never_zero(workspace, deployments, capsys):
    data = as_json(workspace, capsys, "--deployments", str(deployments), *ENVS)
    billing = service(data, "billing")
    assert billing["stages"]["merge_to_tag"]["median_hours"] == 6.0
    assert billing["stages"]["merge_to_prod"] == {"status": "NOT MEASURED"}
    rows = [g for g in data["releases"]["groups"] if g["scope"] == "billing"]
    assert len(rows) == 1
    assert all(s == {"status": "NOT MEASURED"} for s in rows[0]["stages"].values())
    assert data["changes"]["overall_prod_services"] == ["orders"]
    # overall merge->tag pools both services; overall releases only the measured one
    assert data["changes"]["overall"]["merge_to_tag"]["n"] == 5
    assert group(data, "overall", "regular")["units"] == 3


def test_no_deployment_data_at_all(workspace, capsys):
    data = as_json(workspace, capsys)
    assert data["changes"]["overall"]["merge_to_prod"] == {"status": "NOT MEASURED"}
    overall = [g for g in data["releases"]["groups"] if g["scope"] == "overall"]
    assert len(overall) == 1
    assert overall[0]["stages"] == {"tag -> production": {"status": "NOT MEASURED"}}


def test_window_on_production_time_for_releases(workspace, deployments, capsys):
    data = as_json(
        workspace, capsys, "--deployments", str(deployments), *ENVS,
        "--since", "2026-01-07", "--until", "2026-01-08T20:00:00Z",
    )  # fmt: skip
    tags = sorted(u["tag"] for u in data["releases"]["units"])
    assert tags == ["v1.1-hotfix-1"]  # v1.2 reached production at the exclusive end
    assert service(data, "orders")["changes"] == 2  # changes: merge time in the window


def test_deployment_before_its_tag_is_a_data_error(workspace, tmp_path, capsys):
    log = write(
        tmp_path / "deploys.json",
        {
            "deployments": [
                {"tag": "v1.1", "environment": "production", "deployed_at": "2026-01-06T00:00Z"},
                {"tag": "v1.0", "environment": "production", "deployed_at": "2026-01-05T06:00Z"},
            ]
        },
    )
    data = as_json(workspace, capsys, "--deployments", str(log))
    assert data["data_errors"]["deployed_before_tagged"] == {"orders": ["v1.1"]}
    # rows without a service apply to every service: billing's v1.0 went out too
    assert service(data, "billing")["stages"]["merge_to_prod"]["median_hours"] == 54.0
    stage = group(data, "orders", "regular")["stages"]["tag -> production"]
    assert stage["n"] == 1 and stage["pending"] == 1  # v1.1 out of order: dropped
    run(workspace, "--deployments", str(log))
    assert "production before the tag date, ignored: v1.1" in capsys.readouterr().out


def test_deploy_repository_first_pin_counts(workspace, tmp_path, capsys):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    git(deploy, "init", "-q", "-b", "main")
    (deploy / "envs").mkdir()
    commit(deploy, "test v1.0", "2026-01-04T12:00:00Z", "envs/test.yaml", "orders: v1.0\n")
    commit(deploy, "prod v1.0", "2026-01-05T10:00:00Z", "envs/production.yaml",
           "services:\n  orders: {ref: v1.0}\n")  # fmt: skip
    commit(deploy, "test v1.1", "2026-01-06T11:00:00Z", "envs/test.yaml", "orders: v1.1\n")
    commit(deploy, "roll back", "2026-01-06T20:00:00Z", "envs/test.yaml", "orders: v1.0\n")
    commit(deploy, "oops", "2026-01-07T00:00:00Z", "envs/production.yaml", "services: [x\n")
    commit(deploy, "hotfix", "2026-01-07T06:00:00Z", "envs/production.yaml",
           "orders: v1.1-hotfix-1\n")  # fmt: skip
    data = as_json(
        workspace, capsys, "--deploy-repo", str(deploy), "--deploy-file", "envs/{env}.yaml",
        "--envs", "test,production",
    )  # fmt: skip
    regular = group(data, "orders", "regular")["stages"]
    assert regular["test -> production"] == measured(1, 2, 22.0, 22.0, 0, 1)
    assert regular["tag -> production"] == measured(1, 1, 24.0, 24.0, 0, 1)
    units = {u["tag"]: u for u in data["releases"]["units"]}
    assert units["v1.0"]["reached"]["test"] == "2026-01-04T12:00:00+00:00"  # not the roll back
    hotfix = group(data, "orders", "hotfix")["stages"]["tag -> production"]
    assert hotfix["median_hours"] == 17.0
    unreadable = data["data_errors"]["unreadable_deploy_revisions"]
    assert len(unreadable) == 1 and unreadable[0].endswith(":envs/production.yaml")


def test_thresholds_are_configurable(workspace, deployments, capsys):
    data = as_json(workspace, capsys, "--deployments", str(deployments), *ENVS,
                   "--threshold", "12", "--threshold", "1")  # fmt: skip
    stage = group(data, "orders", "regular")["stages"]["test -> acceptance"]
    assert stage["at_or_under"] == {"1h": 1, "12h": 2}


def test_markdown_report(workspace, deployments, capsys):
    run(workspace, "--deployments", str(deployments), "--since", "2026-01-02", "--top", "1", *ENVS)
    out = capsys.readouterr().out
    assert out.startswith("# Release lead time (2026-01-02T00:00:00+00:00 .. end)")
    assert "Medians are not additive" in out
    assert "| Scope | Stage | n | Median | p90 | <= 6h | <= 24h |" in out
    assert "| orders | merge -> tag | 3 (+2 pending) | 24.0h | 2.0d | 0 | 2 |" in out
    assert "| orders | merge -> prod | 4 (+1 pending) | 46.0h | 3.0d | 0 | 0 |" in out
    assert "| billing | merge -> prod | - | NOT MEASURED | NOT MEASURED | - | - |" in out
    assert "Overall merge -> prod covers 1 of 2 service(s): orders." in out
    assert (
        "## Releases (production time in the window; chain test -> acceptance -> production)" in out
    )
    assert "production = earliest of production, production-dr" in out
    assert "| orders | hotfix | tag -> production | 1 | 11.0h | 11.0h | 0 | 1 |" in out
    assert "Lightweight tags (no tag date; environment stages only): orders v1.2." in out
    assert "## Slowest 1 change(s)" in out
    assert (
        "| orders | Merge BUG-1 fix rounding (" in out
        and "| v1.0 | v1.0 | 3.0d (merge -> prod) |" in out
    )


def test_rerun_is_identical_and_output_file(workspace, deployments, tmp_path):
    first, second = tmp_path / "1.md", tmp_path / "2.md"
    for path in (first, second):
        assert run(workspace, "--deployments", str(deployments), *ENVS, "-o", str(path)) == 0
    assert first.read_text() == second.read_text()


def test_manifest_names_services_and_missing_clone_is_reported(workspace, tmp_path, capsys):
    manifest = write(
        tmp_path / "prod.json",
        {"services": {"orders": "v1.2", "catalog": "v1", "billing": {"ref": "x", "repo": "nope"}}},
    )
    code = rl.main(["--workspace", str(workspace), "--manifest", str(manifest), "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    problems = data["services_not_measured"]
    assert sorted(problems) == ["billing", "catalog"]
    assert all(p.startswith("no git clone at") for p in problems.values())
    code = rl.main(["--workspace", str(workspace), "--service", "orders", "--mainline", "dev"])
    assert (
        code == 0 and "**orders not measured:** mainline 'dev' not found" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("content", "suffix", "args", "message"),
    [
        (None, ".csv", [], "deployment log not found"),
        ("tag,environment\nv1,production\n", ".csv", [], "missing deployed_at"),
        ("tag,environment,deployed_at\nv1,production,soon\n", ".csv", [], "not an ISO 8601"),
        ("{not json", ".json", [], "not valid JSON"),
        ('{"deployments": 3}', ".json", [], "expected a list"),
        ("tag,environment,deployed_at\n", ".csv", ["--since", "yesterday"], "--since"),
        ("tag,environment,deployed_at\n", ".csv", ["--top", "-1"], "must not be negative"),
        ("tag,environment,deployed_at\n", ".csv", ["--production", ","], "names no environment"),
        ("tag,environment,deployed_at\n", ".csv", ["--deploy-repo", "/nope"], "not found"),
        ("tag,environment,deployed_at\n", ".csv", ["--key-pattern", "("], ""),
        ("tag,environment,deployed_at\n", ".csv", ["--hotfix-pattern", "("], ""),
    ],
)
def test_bad_input_exits_one(workspace, tmp_path, capsys, content, suffix, args, message):
    log = tmp_path / f"log{suffix}"
    if content is not None:
        log.write_text(content)
    assert run(workspace, "--deployments", str(log), *args) == 1
    assert message in capsys.readouterr().err


def test_no_service_and_empty_name_exit_one(workspace, capsys):
    assert rl.main(["--workspace", str(workspace)]) == 1
    assert rl.main(["--service", "=dir"]) == 1
    err = capsys.readouterr().err
    assert "name at least one --service" in err and "no name" in err


def test_helpers():
    assert rl.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 0.9) == 5.0
    assert rl.percentile([1.0], 0.9) == 1.0
    assert rl.percentile(list(map(float, range(1, 11))), 0.9) == 9.0
    assert rl.fmt_hours(None) == "-" and rl.fmt_hours(47.94) == "47.9h"
    assert rl.fmt_hours(72) == "3.0d"
    assert rl.parse_time("2026-01-01T00:00:00Z", "x") == rl.parse_time("2026-01-01", "x")
    assert rl.release_stages(["a", "b", "production"]) == [
        "a -> b",
        "b -> production",
        "tag -> production",
    ]
    assert rl.split_list(" a, ,b ") == ["a", "b"]
