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


def commit(repo: Path, subject: str, at: str, name: str) -> None:
    (repo / name).write_text(subject + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject, at=at)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """``orders`` mainline, merge->tag->prod hours per change (since 2026-01-02):

    merge BUG-1 (48 / 24 / 72), ORD-2 squash (24 / 24 / 48),
    ORD-3 (24 / 62 / 86, its tag v1.1 never deployed, v1.2 carries it),
    ORD-4 (0 / 48 / 48, light tag), untagged work (pending).
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
    commit(orders, "ORD-4 next change", "2026-01-07T00:00:00Z", "e.txt")
    git(orders, "tag", "v1.2")  # light tag: dated by its commit
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
        "orders,v1.0,staging,2026-01-04T12:00:00Z\n"
        "orders,v1.0,production,2026-01-05T10:00:00Z\n"
        "orders,v1.0,production,2026-01-06T10:00:00Z\n"  # a redeploy: the first one counts
        "orders,v1.2,production,2026-01-09T00:00:00\n"  # no offset: UTC
    )
    return log


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


def test_stages_per_service_with_a_deployment_log(workspace, deployments, capsys):
    data = as_json(workspace, capsys, "--deployments", str(deployments), "--since", "2026-01-02")
    orders = next(s for s in data["services"] if s["service"] == "orders")
    assert orders["changes"] == 5 and orders["untagged"] == 1
    assert orders["stages"] == {
        "merge_to_tag": {
            "status": "measured", "count": 4, "pending": 1, "median_hours": 24.0, "p90_hours": 48.0
        },
        "tag_to_prod": {
            "status": "measured", "count": 4, "pending": 0, "median_hours": 36.0, "p90_hours": 62.0
        },
        "merge_to_prod": {
            "status": "measured", "count": 4, "pending": 1, "median_hours": 60.0, "p90_hours": 86.0
        },
    }  # fmt: skip
    slowest = data["slowest"]
    assert [r["subject"] for r in slowest[:2]] == ["ORD-3 later change", "Merge BUG-1 fix rounding"]
    assert slowest[0]["tag"] == "v1.1" and slowest[0]["deployed_tag"] == "v1.2"
    assert slowest[0]["keys"] == ["ORD-3"] and slowest[0]["ranked_by"] == "merge_to_prod"


def test_service_without_production_rows_is_not_measured_never_zero(workspace, deployments, capsys):
    data = as_json(workspace, capsys, "--deployments", str(deployments))
    billing = next(s for s in data["services"] if s["service"] == "billing")
    assert billing["stages"]["merge_to_tag"]["median_hours"] == 6.0
    assert billing["stages"]["tag_to_prod"] == {"status": "NOT MEASURED"}
    assert billing["stages"]["merge_to_prod"] == {"status": "NOT MEASURED"}
    assert data["overall_prod_services"] == ["orders"]
    # overall merge->tag pools both services: 82, 48, 24, 24, 0 and billing's 6
    assert data["overall"]["merge_to_tag"]["count"] == 6
    assert data["overall"]["merge_to_tag"]["p90_hours"] == 82.0
    assert data["overall"]["merge_to_prod"]["count"] == 5  # orders only


def test_no_deployment_log_means_production_stages_not_measured(workspace, capsys):
    assert run(workspace, "--since", "2026-01-02") == 0
    out = capsys.readouterr().out
    assert "| overall | tag -> prod | NOT MEASURED | NOT MEASURED |" in out
    assert "| orders | merge -> prod | NOT MEASURED | NOT MEASURED |" in out
    assert "| orders | merge -> tag | 24.0h | 2.0d | 4 (+1 pending) |" in out
    assert "no production deployment recorded: production stages NOT MEASURED" in out
    assert "1 change(s) not in any release tag yet" in out


def test_markdown_report(workspace, deployments, capsys):
    run(workspace, "--deployments", str(deployments), "--since", "2026-01-02", "--top", "1")
    out = capsys.readouterr().out
    assert out.startswith("# Release lead time (2026-01-02T00:00:00+00:00 .. end)")
    assert "| orders | tag -> prod | 36.0h | 2.6d | 4 |" in out
    assert "| orders | merge -> prod | 2.5d | 3.6d | 4 (+1 pending) |" in out
    assert "Overall production stages cover 1 of 2 service(s): orders." in out
    assert "## Slowest 1 change(s)" in out
    assert (
        "| orders | ORD-3 later change (" in out and "| v1.1 | v1.2 | 3.6d (merge -> prod) |" in out
    )


def test_window_and_tag_selection(workspace, deployments, capsys):
    data = as_json(
        workspace, capsys, "--since", "2026-01-05", "--until", "2026-01-07T00:00:00Z",
        "--tags", "v1.1",
    )  # fmt: skip
    orders = next(s for s in data["services"] if s["service"] == "orders")
    assert orders["changes"] == 1  # ORD-3 only: the until bound is exclusive
    assert orders["stages"]["merge_to_tag"]["median_hours"] == 24.0
    billing = next(s for s in data["services"] if s["service"] == "billing")
    assert billing["changes"] == 0 and billing["stages"]["merge_to_tag"]["status"] == "no data"


def test_unscoped_rows_apply_to_every_service_and_bad_rows_are_ignored(workspace, tmp_path, capsys):
    log = write(
        tmp_path / "deploys.json",
        {
            "deployments": [
                {"tag": "v1.0", "environment": "prod", "deployed_at": "2026-01-02T00:00:00Z"},
                {"tag": "v1.0", "environment": "prod", "deployed_at": "2026-01-05T06:00:00Z"},
            ]
        },
    )
    data = as_json(workspace, capsys, "--deployments", str(log), "--production", "prod")
    by_name = {s["service"]: s for s in data["services"]}
    # the first row predates both v1.0 tags: a data error, ignored and named
    assert by_name["orders"]["deployed_before_tagged"] == ["v1.0"]
    assert by_name["billing"]["deployed_before_tagged"] == ["v1.0"]
    assert by_name["billing"]["stages"]["tag_to_prod"]["median_hours"] == 48.0
    run(workspace, "--deployments", str(log), "--production", "prod")
    assert "deployment dated before its tag, ignored: v1.0" in capsys.readouterr().out


def test_rerun_is_identical_and_output_file(workspace, deployments, tmp_path):
    first, second = tmp_path / "1.md", tmp_path / "2.md"
    for path in (first, second):
        assert run(workspace, "--deployments", str(deployments), "-o", str(path)) == 0
    assert first.read_text() == second.read_text()


def test_manifest_names_services_and_missing_clone_is_reported(workspace, tmp_path, capsys):
    manifest = write(
        tmp_path / "prod.json",
        {"services": {"orders": "v1.2", "catalog": "v1", "billing": {"ref": "x", "repo": "nope"}}},
    )
    code = rl.main(["--workspace", str(workspace), "--manifest", str(manifest), "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    problems = {s["service"]: s["problem"] for s in data["services"]}
    assert problems["orders"] is None
    assert problems["catalog"].startswith("no git clone at")
    assert problems["billing"].startswith("no git clone at")
    code = rl.main(["--workspace", str(workspace), "--service", "orders", "--mainline", "dev"])
    assert code == 0 and "**Not measured:** mainline 'dev' not found" in capsys.readouterr().out


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
        ("tag,environment,deployed_at\n", ".csv", ["--key-pattern", "("], ""),
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
