"""Promotion diff on temporary git repositories: shipped keys, noise, the inversion check."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.promotion_diff import promotion_diff as pdiff

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")

KEY_RE = re.compile(pdiff.DEFAULT_KEY_PATTERN)


def git(repo: Path, *args: str) -> str:
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
    ).stdout.strip()


def commit(repo: Path, subject: str, **files: str) -> str:
    for name, content in files.items():
        (repo / name).write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """``orders``: v1.1 (FROM) is main; v1.0.1 (TO) is a hotfix line off v1.0."""
    ws = tmp_path / "ws"
    orders = ws / "orders"
    orders.mkdir(parents=True)
    git(orders, "init", "-q", "-b", "main")
    commit(orders, "initial", **{"app.txt": "1\n", "shared.txt": "a\n"})
    git(orders, "tag", "v1.0")
    fix = commit(orders, "BUG-142 fix rounding", **{"round.txt": "fixed\n"})
    commit(orders, "chore(deps): bump parser to 2.1", **{"deps.txt": "2.1\n"})
    commit(orders, "add export endpoint", **{"export.txt": "v2\n"})
    # same end content for shared.txt as the hotfix below, but a different patch
    commit(orders, "align shared file", **{"shared.txt": "b\n", "app.txt": "2\n"})
    commit(orders, "ORD-42 ORD-43 new totals", **{"totals.txt": "new\n"})
    git(orders, "tag", "v1.1")

    git(orders, "checkout", "-q", "-b", "hotfix", "v1.0")
    commit(orders, "add export endpoint", **{"export.txt": "v1\n"})
    git(orders, "cherry-pick", fix)  # after another commit, so it gets its own sha
    commit(orders, "hotfix shared value", **{"shared.txt": "b\n"})
    commit(orders, "BUG-150 hotfix null check", **{"hot.txt": "guard\n"})
    git(orders, "tag", "v1.0.1")
    git(orders, "checkout", "-q", "main")

    billing = ws / "billing-service"
    billing.mkdir()
    git(billing, "init", "-q", "-b", "main")
    commit(billing, "initial", **{"b.txt": "1\n"})
    git(billing, "tag", "v3.0")
    return ws


def write(path: Path, data) -> Path:
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def manifests(tmp_path: Path) -> tuple[Path, Path]:
    source = write(
        tmp_path / "staging.json",
        {
            "services": {
                "orders": "v1.1",
                "billing": {"ref": "v3.0", "repo": "billing-service"},
                "search": "v0.1",
            }
        },
    )
    target = write(
        tmp_path / "production.json",
        {"orders": "v1.0.1", "billing": {"ref": "v3.0", "repo": "billing-service"}},
    )
    return source, target


def run(workspace, manifests, *extra, capsys=None):
    source, target = manifests
    code = pdiff.main(
        ["--from", str(source), "--to", str(target), "--workspace", str(workspace), "--no-fetch"]
        + list(extra)
    )
    return code


def test_report_lists_keys_noise_and_unkeyed(workspace, manifests, capsys):
    assert run(workspace, manifests) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Promotion diff: staging -> production")
    assert "| orders | v1.1 | v1.0.1 | 5 | 3 | 1 |" in out
    assert "- BUG-142: BUG-142 fix rounding" in out
    assert "- ORD-42: ORD-42 ORD-43 new totals" in out and "- ORD-43:" in out
    assert "Unkeyed changes (2):" in out and "- add export endpoint" in out
    assert "Noise: 1 commit(s)" in out
    assert "## billing\n\nNo change" in out


def test_inversion_check_clears_equivalents_and_flags_the_hotfix(workspace, manifests, capsys):
    run(workspace, manifests)
    out = capsys.readouterr().out
    assert "**Would regress 1 commit(s) only production has:**" in out
    assert "- BUG-150 hotfix null check" in out and "(in tags: v1.0.1)" in out
    assert "BUG-142 fix rounding (" in out and ": same patch" in out
    assert "add export endpoint (" in out and ": same subject" in out
    assert "hotfix shared value (" in out and ": identical files" in out


def test_services_that_cannot_be_compared_are_named(workspace, manifests, tmp_path, capsys):
    source, target = manifests
    write(source, {"orders": "v1.1", "catalog": "v1", "search": "v0.1"})
    write(target, {"orders": "v1.1", "catalog": "v1", "legacy": "v9"})
    assert run(workspace, (source, target)) == 0
    out = capsys.readouterr().out
    assert "## catalog\n\n**Not compared:** no git clone at" in out
    assert "## search\n\n**Not compared:** not in TO" not in out  # no clone: reported as such
    assert "## legacy\n\n**Not compared:** not in FROM" in out
    assert "| catalog | v1 | v1 | ? | ? | ? |" in out


def test_missing_refs_are_reported_not_raised(workspace, manifests, capsys):
    source, target = manifests
    write(source, {"orders": "v9.9"})
    write(target, {"orders": "v1.0"})
    run(workspace, (source, target))
    assert "FROM ref 'v9.9' not found" in capsys.readouterr().out
    write(source, {"orders": "v1.1"})
    write(target, {"orders": "nope"})
    run(workspace, (source, target))
    assert "TO ref 'nope' not found" in capsys.readouterr().out


def test_new_service_with_a_clone(workspace, manifests, capsys):
    source, target = manifests
    write(source, {"orders": "v1.1"})
    write(target, {})
    assert run(workspace, (source, target), "--strict") == 0
    assert "not in TO: new service" in capsys.readouterr().out


def test_strict_exits_two_on_regression_or_gap(workspace, manifests, capsys):
    assert run(workspace, manifests, "--strict") == 2
    source, target = manifests
    write(source, {"orders": "v1.1"})
    write(target, {"orders": "v1.0"})  # target is an ancestor: nothing to regress
    assert run(workspace, (source, target), "--strict") == 0
    write(target, {"orders": "v1.0", "catalog": "v1"})
    write(source, {"orders": "v1.1", "catalog": "v1"})
    assert run(workspace, (source, target), "--strict") == 2
    capsys.readouterr()


def test_keys_mode_prints_only_shipped_keys(workspace, manifests, capsys):
    assert run(workspace, manifests, "--keys") == 0
    assert capsys.readouterr().out == "BUG-142\nORD-42\nORD-43\n"


def test_custom_key_pattern(workspace, manifests, capsys):
    run(workspace, manifests, "--keys", "--key-pattern", r"\bORD-\d+")
    assert capsys.readouterr().out == "ORD-42\nORD-43\n"


def test_test_root_maps_keys_to_mentions(workspace, manifests, tmp_path, capsys):
    tests = tmp_path / "suite"
    tests.mkdir()
    (tests / "test_totals.py").write_text("# covers ORD-42\n")
    (tests / "image.png").write_bytes(b"ORD-43")
    run(workspace, manifests, "--test-root", str(tests))
    out = capsys.readouterr().out
    assert f"(tests mentioning it: {(tests / 'test_totals.py').as_posix()})" in out
    assert "- ORD-43: ORD-42 ORD-43 new totals (" in out and "(no test mentions it)" in out


def test_show_column_and_output_file(workspace, manifests, tmp_path):
    shown = write(tmp_path / "qa.json", {"orders": "v1.0"})
    report = tmp_path / "out" / "delta.md"
    report.parent.mkdir()
    assert run(workspace, manifests, "--show", str(shown), "-o", str(report)) == 0
    text = report.read_text()
    assert "| Service | staging | production | qa | Commits |" in text
    assert "| orders | v1.1 | v1.0.1 | v1.0 |" in text
    assert "| billing | v3.0 | v3.0 | - |" in text


def test_yaml_manifests(workspace, tmp_path, capsys):
    source = tmp_path / "from.yaml"
    source.write_text("services:\n  orders: v1.1\n")
    target = tmp_path / "to.yml"
    target.write_text("orders:\n  ref: v1.0\n")
    assert run(workspace, (source, target)) == 0
    assert "| orders | v1.1 | v1.0 | 5 | 3 | 0 |" in capsys.readouterr().out


def test_noise_file_is_configurable(workspace, manifests, tmp_path, capsys):
    noise = tmp_path / "noise.txt"
    noise.write_text("# nothing but exports\n\n^add export\n")
    run(workspace, manifests, "--noise-file", str(noise))
    out = capsys.readouterr().out
    assert "Unkeyed changes (2):" in out and "- chore(deps): bump parser" in out


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda t, s, n: s.unlink(), "manifest not found"),
        (lambda t, s, n: s.write_text("{not json"), "not valid JSON"),
        (lambda t, s, n: s.write_text("[1, 2]"), "expected a mapping"),
        (lambda t, s, n: s.write_text('{"orders": ""}'), "has no ref"),
        (lambda t, s, n: n.write_text("(unclosed\n"), "bad pattern"),
    ],
)
def test_bad_input_exits_one(workspace, manifests, tmp_path, capsys, setup, message):
    source, _ = manifests
    noise = tmp_path / "noise.txt"
    noise.write_text("bump\n")
    setup(tmp_path, source, noise)
    assert run(workspace, manifests, "--noise-file", str(noise)) == 1
    assert message in capsys.readouterr().err


def test_missing_test_root_and_bad_key_pattern_exit_one(workspace, manifests, tmp_path, capsys):
    assert run(workspace, manifests, "--test-root", str(tmp_path / "none")) == 1
    assert run(workspace, manifests, "--key-pattern", "(") == 1
    capsys.readouterr()


def test_fetch_is_skipped_without_a_remote_and_warns_when_it_fails(workspace, manifests, capsys):
    assert run(workspace, manifests) == 0  # --no-fetch
    source, target = manifests
    code = pdiff.main(["--from", str(source), "--to", str(target), "--workspace", str(workspace)])
    assert code == 0 and "warning" not in capsys.readouterr().err
    git(workspace / "orders", "remote", "add", "origin", str(workspace / "gone"))
    pdiff.main(["--from", str(source), "--to", str(target), "--workspace", str(workspace)])
    assert "warning: fetch failed for orders" in capsys.readouterr().err


def test_split_and_noise_helpers():
    noise = pdiff.load_noise(pdiff.DEFAULT_NOISE_FILE)
    commits = [
        pdiff.Commit("a" * 40, "BUG-1 chore(deps): bump x"),
        pdiff.Commit("b" * 40, "chore(deps): bump y"),
        pdiff.Commit("c" * 40, "fix the thing"),
        pdiff.Commit("d" * 40, "Update all non-major dependencies"),
    ]
    keyed, unkeyed, noisy = pdiff.split_commits(commits, KEY_RE, noise)
    assert list(keyed) == ["BUG-1"]  # a key wins over a noise pattern
    assert [c.subject for c in unkeyed] == ["fix the thing"]
    assert len(noisy) == 2
    assert pdiff.keys_in("see BUG-1, BUG-1 and X-2", KEY_RE) == ["BUG-1"]
    assert commits[0].short == "a" * 8


def test_markdown_cells_are_escaped():
    assert pdiff._md("a|b\nc") == "a\\|b c"
