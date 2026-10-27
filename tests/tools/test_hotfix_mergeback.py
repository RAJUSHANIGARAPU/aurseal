"""Hotfix merge-back on temporary git repositories: what production runs that main lacks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.tools.test_promotion_diff import commit, git, write
from tools.hotfix_mergeback import hotfix_mergeback as hm

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """``orders``: production runs v1.0.1, a hotfix line off v1.0; main moved on."""
    ws = tmp_path / "ws"
    orders = ws / "orders"
    orders.mkdir(parents=True)
    git(orders, "init", "-q", "-b", "main")
    commit(orders, "initial", **{"app.txt": "1\n", "shared.txt": "a\n"})
    git(orders, "tag", "v1.0")

    git(orders, "checkout", "-q", "-b", "hotfix")
    commit(orders, "BUG-142 fix rounding", **{"round.txt": "fixed\n"})
    commit(orders, "add export endpoint", **{"export.txt": "v1\n"})
    commit(orders, "hotfix shared value", **{"shared.txt": "b\n"})
    commit(orders, "BUG-150 hotfix null check", **{"hot.txt": "guard\n"})
    commit(orders, "guard empty basket", **{"basket.txt": "guard\n"})
    commit(orders, "chore(deps): bump parser to 2.2", **{"deps.txt": "2.2\n"})
    git(orders, "tag", "v1.0.1")
    fix = git(orders, "rev-parse", "v1.0.1~5")

    git(orders, "checkout", "-q", "main")
    commit(orders, "ORD-7 new totals", **{"totals.txt": "new\n"})
    git(orders, "cherry-pick", fix)  # same patch, its own sha
    commit(orders, "add export endpoint", **{"export.txt": "v2\n"})  # same subject
    commit(orders, "align shared file", **{"shared.txt": "b\n", "app.txt": "2\n"})  # same files

    billing = ws / "billing-service"
    billing.mkdir()
    git(billing, "init", "-q", "-b", "main")
    commit(billing, "initial", **{"b.txt": "1\n"})
    git(billing, "tag", "v3.0")
    commit(billing, "later work", **{"b.txt": "2\n"})
    return ws


@pytest.fixture
def production(tmp_path: Path) -> Path:
    return write(
        tmp_path / "production.json",
        {
            "services": {
                "orders": "v1.0.1",
                "billing": {"ref": "v3.0", "repo": "billing-service"},
            }
        },
    )


def run(workspace: Path, manifest: Path, *extra: str) -> int:
    return hm.main(
        ["--production", str(manifest), "--workspace", str(workspace), "--no-fetch", *extra]
    )


def test_report_lists_production_only_fixes_and_clears_equivalents(workspace, production, capsys):
    assert run(workspace, production) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Hotfix merge-back: production -> main")
    assert "**2 commit(s) running in production are not on main** across 1 service(s)" in out
    assert "1 more match the noise patterns" in out
    assert "| orders | v1.0.1 | 2 | BUG-150 | 1 | 3 |" in out
    assert "- BUG-150: BUG-150 hotfix null check (" in out and "(in tags: v1.0.1)" in out
    assert "- guard empty basket (" in out
    assert "Noise, not on main (1):\n- chore(deps): bump parser to 2.2" in out
    assert "BUG-142 fix rounding (" in out and ": same patch" in out
    assert "add export endpoint (" in out and ": same subject" in out
    assert "hotfix shared value (" in out and ": identical files" in out
    assert "## billing\n\nClean: everything production runs is on main." in out
    assert "| billing | v3.0 | 0 | - | 0 | 0 |" in out


def test_json_output(workspace, production, capsys):
    assert run(workspace, production, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["headline"] == {
        "missing": 2,
        "noise": 1,
        "services_affected": 1,
        "services_not_checked": 0,
    }
    orders = next(s for s in data["services"] if s["service"] == "orders")
    assert sorted(m["subject"] for m in orders["missing"]) == [
        "BUG-150 hotfix null check",
        "guard empty basket",
    ]
    assert ["BUG-150"] in [m["keys"] for m in orders["missing"]]
    assert {c["equivalence"] for c in orders["already_on_mainline"]} == {
        "same patch",
        "same subject",
        "identical files",
    }
    assert orders["noise"][0]["subject"].startswith("chore(deps)")


def test_fail_on_missing_exits_two_only_when_something_is_missing(
    workspace, production, tmp_path, capsys
):
    assert run(workspace, production, "--fail-on-missing") == 2
    clean = write(tmp_path / "clean.json", {"billing-service": "v3.0"})
    assert run(workspace, clean, "--fail-on-missing") == 0
    capsys.readouterr()


def test_noise_alone_does_not_fail(workspace, tmp_path, capsys):
    orders = workspace / "orders"
    git(orders, "checkout", "-q", "-b", "deps-only", "v1.0")
    commit(orders, "chore(deps): bump lib", **{"lib.txt": "9\n"})
    git(orders, "tag", "v1.0.2")
    git(orders, "checkout", "-q", "main")
    manifest = write(tmp_path / "prod.json", {"orders": "v1.0.2"})
    assert run(workspace, manifest, "--fail-on-missing") == 0
    assert "**0 commit(s)" in capsys.readouterr().out


def test_unchecked_services_are_named_and_fail_the_gate(workspace, tmp_path, capsys):
    manifest = write(
        tmp_path / "prod.json", {"orders": "nope", "catalog": "v1", "billing-service": "v3.0"}
    )
    assert run(workspace, manifest, "--fail-on-missing") == 2
    out = capsys.readouterr().out
    assert "**Not checked:** no git clone at" in out
    assert "production ref 'nope' not found" in out
    assert "**2 service(s) not checked**" in out
    assert "| catalog | v1 | ? | ? | ? | ? |" in out
    manifest = write(tmp_path / "p.json", {"orders": "v1.0.1"})
    assert run(workspace, manifest, "--mainline", "dev") == 0  # reported, gate not asked
    assert "mainline 'dev' not found" in capsys.readouterr().out


def test_remote_tracking_mainline_wins_over_a_stale_local_branch(workspace, production, capsys):
    orders = workspace / "orders"
    git(orders, "checkout", "-q", "-b", "merged", "main")
    git(orders, "merge", "-q", "-s", "ours", "--no-edit", "v1.0.1")
    git(orders, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(orders, "checkout", "-q", "main")
    run(workspace, production)
    assert "## orders\n\nClean" in capsys.readouterr().out


def test_custom_mainline_key_pattern_and_output_file(workspace, production, tmp_path):
    report = tmp_path / "out.md"
    code = run(
        workspace, production, "--mainline", "hotfix", "--key-pattern", r"\bORD-\d+", "-o",
        str(report),
    )  # fmt: skip
    assert code == 0
    text = report.read_text()
    assert "# Hotfix merge-back: production -> hotfix" in text
    assert "**0 commit(s) running in production are not on hotfix**" in text


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--production", "missing.json"], "manifest not found"),
        (["--key-pattern", "("], ""),
        (["--noise-file", "missing.txt"], ""),
    ],
)
def test_bad_input_exits_one(workspace, production, args, message, capsys):
    argv = ["--production", str(production), "--workspace", str(workspace), "--no-fetch", *args]
    assert hm.main(argv) == 1
    assert message in capsys.readouterr().err


def test_fetch_failure_warns_but_still_reports(workspace, production, capsys):
    git(workspace / "orders", "remote", "add", "origin", str(workspace / "gone"))
    hm.main(["--production", str(production), "--workspace", str(workspace)])
    captured = capsys.readouterr()
    assert "warning: fetch failed for orders" in captured.err
    assert "BUG-150 hotfix null check" in captured.out
