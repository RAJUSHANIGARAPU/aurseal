"""Hotfix merge-back on temporary git repositories: what production runs that default lacks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.tools.test_promotion_diff import write
from tests.tools.test_release_leadtime import git
from tools.hotfix_mergeback import hotfix_mergeback as hm

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")

DAY = "2026-01-01T00:00:00Z"


def commit(repo: Path, subject: str, **files: str) -> str:
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def tag(repo: Path, name: str, at: str = DAY) -> None:
    git(repo, "tag", "-a", name, "-m", name, at=at)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """``orders`` v1.0.0-hotfix-2 holds one fix per matching rule; ``billing`` two edge tags."""
    ws = tmp_path / "ws"
    orders = ws / "orders"
    orders.mkdir(parents=True)
    git(orders, "init", "-q", "-b", "main")
    commit(orders, "initial", **{"app.txt": "1\n", "shared.txt": "a\n"})
    tag(orders, "v1.0.0")

    git(orders, "checkout", "-q", "-b", "hotfix")
    commit(orders, "BUG-142 fix rounding", **{"round.txt": "fixed\n"})
    commit(orders, "BUG-150 hotfix null check", **{"hot.txt": "guard\n"})
    git(orders, "tag", "rel-1")  # not a hotfix tag: its base is the merge-base
    tag(orders, "v1.0.0-hotfix-1", "2026-02-02T00:00:00Z")
    commit(orders, "ORD-7 totals fix", **{"totals.txt": "X\n"})
    commit(orders, "chore: tweak ci", **{".github/workflows/ci.yml": "on: push\n"})
    commit(orders, "docs and tests", **{"docs/a.md": "a\n", "tests/test_a.py": "x = 1\n"})
    bad = commit(orders, "BUG-160 bad idea", **{"bad.txt": "bad\n"})
    git(orders, "revert", "--no-edit", bad)
    basket = commit(orders, "guard empty basket", **{"basket.txt": "guard\n"})
    commit(orders, "fix: Tidy logging (#12)", **{"log.txt": "a\n"})
    commit(orders, "guard null cart", **{"cart.txt": "guard\n"})
    tag(orders, "v1.0.0-hotfix-2", "2026-02-10T00:00:00Z")

    git(orders, "checkout", "-q", "main")
    git(orders, "checkout", "-q", "-b", "backport")
    commit(orders, "BUG-142 back-port rounding", **{"round.txt": "fixed differently\n"})
    git(orders, "checkout", "-q", "main")
    git(orders, "merge", "-q", "--no-ff", "-m", "Merge BUG-142 back-port", "backport")
    commit(orders, "ORD-99 reworked totals", **{"totals.txt": "X\n"})
    git(orders, "cherry-pick", basket)
    commit(orders, "tidy logging", **{"log.txt": "b\n"})

    billing = ws / "billing"
    billing.mkdir()
    git(billing, "init", "-q", "-b", "main")
    commit(billing, "initial", **{"b.txt": "1\n"})
    tag(billing, "v3.0.0")
    tag(billing, "v3.1.0")
    git(billing, "checkout", "-q", "-b", "hf")
    commit(billing, "BIL-5 fix", **{"fix.txt": "1\n"})
    tag(billing, "v3.1.0-hotfix-1", "2026-02-05T00:00:00Z")
    git(billing, "checkout", "-q", "main")
    git(billing, "merge", "-q", "--no-ff", "-m", "Merge hf", "hf")
    git(billing, "checkout", "-q", "-b", "ci", "v3.0.0")
    commit(billing, "ci only", **{"Jenkinsfile": "pipeline {}\n"})
    tag(billing, "v3.0.0-hotfix-1", "2026-01-15T00:00:00Z")
    git(billing, "checkout", "-q", "main")
    return ws


def run(workspace: Path, *extra: str) -> int:
    return hm.main(["--workspace", str(workspace), "--no-fetch", *extra])


def as_json(workspace: Path, capsys, *extra: str) -> dict:
    assert run(workspace, "--json", *extra) == 0
    return json.loads(capsys.readouterr().out)


def entry_of(data: dict, service: str, tag_name: str) -> dict:
    return next(t for t in data["tags"] if (t["service"], t["tag"]) == (service, tag_name))


def test_every_matching_rule_on_one_hotfix_tag(workspace, capsys):
    data = as_json(workspace, capsys, "--tag", "orders:v1.0.0-hotfix-2")
    entry = entry_of(data, "orders", "v1.0.0-hotfix-2")
    assert entry["verdict"] == "MISSING"
    assert entry["base_kind"] == "release tag v1.0.0"
    methods = {u["unit"]: u["method"] for u in entry["units"]}
    assert methods == {
        "BUG-142": "key",
        "BUG-150": None,
        "ORD-7": "content",
        "fix: Tidy logging (#12)": "subject",
        "guard empty basket": "patch-id",
        "guard null cart": None,
    }
    evidence = {u["unit"]: (u["evidence"] or {}).get("subject") for u in entry["units"]}
    assert evidence["BUG-142"] == "Merge BUG-142 back-port"  # the merge, not the commit
    assert evidence["guard empty basket"] == "guard empty basket"
    assert evidence["fix: Tidy logging (#12)"] == "tidy logging"
    assert [c["subject"] for c in entry["not_needed"]] == ["chore: tweak ci", "docs and tests"]
    assert len(entry["reverted"]) == 2 and "BUG-160" not in methods


def test_hotfix_tags_are_cumulative_from_the_release_tag(workspace, capsys):
    data = as_json(workspace, capsys, "--tag", "orders:v1.0.0-hotfix-1")
    entry = entry_of(data, "orders", "v1.0.0-hotfix-1")
    assert [u["unit"] for u in entry["units"]] == ["BUG-142", "BUG-150"]
    assert entry["verdict"] == "MISSING"


def test_a_tag_that_is_not_a_hotfix_starts_from_the_merge_base(workspace, capsys):
    data = as_json(workspace, capsys, "--tag", "orders:rel-1")
    entry = entry_of(data, "orders", "rel-1")
    assert entry["base_kind"] == "merge-base"
    assert [u["unit"] for u in entry["units"]] == ["BUG-142", "BUG-150"]


def test_back_ported_not_needed_and_error_verdicts(workspace, capsys):
    data = as_json(
        workspace,
        capsys,
        "--tag", "billing:v3.1.0-hotfix-1",
        "--tag", "billing:v3.0.0-hotfix-1",
        "--tag", "catalog:v1.0.0",
        "--tag", "orders:v9.9.9",
    )  # fmt: skip
    verdicts = {(t["service"], t["tag"]): t["verdict"] for t in data["tags"]}
    assert verdicts == {
        ("billing", "v3.0.0-hotfix-1"): "NOT-NEEDED",
        ("billing", "v3.1.0-hotfix-1"): "BACK-PORTED",
        ("catalog", "v1.0.0"): "ERROR",
        ("orders", "v9.9.9"): "ERROR",
    }
    back = entry_of(data, "billing", "v3.1.0-hotfix-1")
    assert back["units"][0]["method"] == "same commit"
    assert data["headline"]["ERROR"] == 2 and data["headline"]["MISSING"] == 0


def test_markdown_report_names_evidence(workspace, capsys):
    code = run(workspace, "--tag", "orders:v1.0.0-hotfix-2", "--tag", "billing:v3.0.0-hotfix-1")
    assert code == 0
    out = capsys.readouterr().out
    assert "**1 tag(s) MISSING** (2 unit(s) not on the default branch), 0 BACK-PORTED," in out
    assert "| orders | v1.0.0-hotfix-2 | release tag v1.0.0 (" in out
    assert "| **MISSING** | 6 | BUG-150, guard null cart |" in out
    assert "| billing | v3.0.0-hotfix-1 |" in out and "| **NOT-NEEDED** | 0 | - |" in out
    assert "- BUG-142: found by key in " in out and "Merge BUG-142 back-port (" in out
    assert "- **BUG-150: not found** (" in out
    assert '- **"guard null cart": not found** (' in out
    assert "- ORD-7: found by content (" in out
    assert "- not needed (non-product files only): 2 commit(s)" in out
    assert "- reverted inside the range: 2 commit(s) dropped" in out


def test_fail_on_missing_is_opt_in(workspace, capsys):
    assert run(workspace, "--tag", "orders:v1.0.0-hotfix-1") == 0
    assert run(workspace, "--tag", "orders:v1.0.0-hotfix-1", "--fail-on-missing") == 2
    assert run(workspace, "--tag", "billing:v3.1.0-hotfix-1", "--fail-on-missing") == 0
    assert run(workspace, "--tag", "catalog:v1", "--fail-on-missing") == 2
    capsys.readouterr()


def test_window_over_hotfix_tag_dates_skips_copied_clones(workspace, capsys):
    git(workspace, "clone", "-q", str(workspace / "orders"), str(workspace / "orders-copy"))
    data = as_json(workspace, capsys, "--since", "2026-02-01", "--until", "2026-02-06")
    assert [(t["service"], t["tag"]) for t in data["tags"]] == [
        ("billing", "v3.1.0-hotfix-1"),
        ("orders", "v1.0.0-hotfix-1"),
    ]
    assert data["skipped_clones"] == ["orders-copy (a copy of orders)"]


def test_release_manifest_takes_only_hotfix_versions_production_takes_all(
    workspace, tmp_path, capsys
):
    manifest = write(
        tmp_path / "release.json",
        {"services": {"orders": "v1.0.0-hotfix-1", "billing": "v3.1.0"}},
    )
    data = as_json(workspace, capsys, "--release-manifest", str(manifest))
    assert [(t["service"], t["tag"]) for t in data["tags"]] == [("orders", "v1.0.0-hotfix-1")]
    data = as_json(workspace, capsys, "--production", str(manifest))
    assert [t["tag"] for t in data["tags"]] == ["v3.1.0", "v1.0.0-hotfix-1"]
    assert entry_of(data, "billing", "v3.1.0")["verdict"] == "NOT-NEEDED"


def test_default_branch_resolution(workspace, capsys):
    orders = workspace / "orders"
    # origin/HEAD wins: point it at a commit that carries the whole hotfix line
    git(orders, "checkout", "-q", "-b", "merged", "main")
    git(orders, "merge", "-q", "-s", "ours", "--no-edit", "v1.0.0-hotfix-2")
    git(orders, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(orders, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(orders, "checkout", "-q", "main")
    data = as_json(workspace, capsys, "--tag", "orders:v1.0.0-hotfix-1")
    assert entry_of(data, "orders", "v1.0.0-hotfix-1")["verdict"] == "BACK-PORTED"
    # an explicit branch overrides it
    data = as_json(
        workspace, capsys, "--tag", "orders:v1.0.0-hotfix-1", "--default-branch", "hotfix"
    )
    assert entry_of(data, "orders", "v1.0.0-hotfix-1")["verdict"] == "BACK-PORTED"
    # master when there is no main
    billing = workspace / "billing"
    git(billing, "branch", "-m", "main", "master")
    data = as_json(workspace, capsys, "--tag", "billing:v3.1.0-hotfix-1")
    assert entry_of(data, "billing", "v3.1.0-hotfix-1")["verdict"] == "BACK-PORTED"
    git(billing, "branch", "-m", "master", "trunk")
    data = as_json(workspace, capsys, "--tag", "billing:v3.1.0-hotfix-1")
    assert "default branch" in entry_of(data, "billing", "v3.1.0-hotfix-1")["problem"]


def test_clone_base_clones_a_missing_service_blobless(workspace, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    base = f"file://{workspace}"
    data = as_json(empty, capsys, "--tag", "billing:v3.1.0-hotfix-1", "--clone-base", base)
    assert entry_of(data, "billing", "v3.1.0-hotfix-1")["verdict"] == "BACK-PORTED"
    assert git(empty / "billing", "config", "remote.origin.partialclonefilter") == "blob:none"
    data = as_json(empty, capsys, "--tag", "nothing:v1", "--clone-base", base)
    assert "clone of" in entry_of(data, "nothing", "v1")["problem"]


def test_key_prefixes_limit_what_counts_as_a_key(workspace, capsys):
    data = as_json(workspace, capsys, "--tag", "orders:v1.0.0-hotfix-2", "--key-prefix", "ORD")
    units = {u["unit"]: u for u in entry_of(data, "orders", "v1.0.0-hotfix-2")["units"]}
    assert units["ORD-7"]["keyed"] and not units["BUG-150 hotfix null check"]["keyed"]
    assert units["BUG-142 fix rounding"]["method"] is None  # unkeyed: no key match any more


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "name what to check"),
        (["--tag", "orders"], "expected REPO:TAG"),
        (["--production", "missing.json"], "manifest not found"),
        (["--tag", "a:b", "--non-product-file", "missing.txt"], "glob file not found"),
        (["--since", "soon"], "not an ISO 8601"),
        (["--tag", "a:b", "--key-pattern", "("], ""),
    ],
)
def test_bad_input_exits_one(workspace, args, message, capsys):
    assert run(workspace, *args) == 1
    assert message in capsys.readouterr().err


def test_output_file_and_fetch_warning(workspace, tmp_path, capsys):
    git(workspace / "orders", "remote", "add", "origin", str(workspace / "gone"))
    report = tmp_path / "out.md"
    code = hm.main(
        ["--workspace", str(workspace), "--tag", "orders:v1.0.0-hotfix-1", "-o", str(report)]
    )
    assert code == 0 and "BUG-150" in report.read_text()
    assert "warning: fetch failed for orders" in capsys.readouterr().err


def test_revert_pairs_and_revert_of_revert():
    a = hm.Commit("a" * 40, "add flag")
    r1 = hm.Commit("b" * 40, 'Revert "add flag"', f"This reverts commit {'a' * 40}.")
    r2 = hm.Commit("c" * 40, 'Revert "Revert "add flag""', f"This reverts commit {'b' * 40}.")
    kept, dropped = hm.drop_revert_pairs([a, r1, r2])
    assert kept == [r2] and dropped == [a, r1]  # the change is back, as r2
    kept, dropped = hm.drop_revert_pairs([r1])  # reverts something outside the range
    assert kept == [r1] and dropped == []


def test_helpers():
    assert hm.normalise_subject("fix(api): Tidy logging (#12)") == "tidy logging"
    assert hm.normalise_subject("[cherry-pick] hotfix: Guard null cart") == "guard null cart"
    assert hm.normalise_subject("Backport: fix: tidy  Logging!") == "tidy logging"
    globs = hm.load_globs(hm.DEFAULT_NON_PRODUCT_FILE)
    assert hm.is_non_product(["docs/x.md", "src/test/FooTest.java", "poetry.lock"], globs)
    assert not hm.is_non_product(["src/app.py", "docs/x.md"], globs)
    assert not hm.is_non_product([], globs)
    assert hm.key_regex(["ORD", "B.X"], "").pattern == r"\b(?:ORD|B\.X)-\d+\b"
    assert hm.parse_target("svc:v1:x") == hm.Target("svc", "v1:x", "svc")
