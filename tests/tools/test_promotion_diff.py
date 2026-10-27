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
    assert "| orders | v1.1 | v1.0.1 | 4 | 2 | 1 | 1 already in target |" in out
    # the fix production already carries under another sha is counted, not shipped
    assert "- BUG-142:" not in out and "1 already in target under another SHA." in out
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
    assert capsys.readouterr().out == "ORD-42\nORD-43\n"


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
    assert "| orders | v1.1 | v1.0 | 5 | 3 | 0 | - |" in capsys.readouterr().out


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


# ---------------------------------------------------------------- deeper checks


def new_repo(ws: Path, name: str = "orders") -> Path:
    path = ws / name
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    commit(path, "initial", **{"a.txt": "1\n"})
    git(path, "tag", "v1.0")
    return path


def pins(tmp_path: Path, source: dict, target: dict) -> tuple[Path, Path]:
    return write(tmp_path / "staging.json", source), write(tmp_path / "production.json", target)


def test_build_number_ref_resolves_its_sha(workspace, tmp_path, capsys):
    sha = git(workspace / "orders", "rev-parse", "v1.1")
    git_ = pdiff.Git(workspace / "orders")
    assert git_.resolve(f"123-{sha[:7]}") == sha
    assert git_.resolve(f"build-77-{sha}") == sha
    assert git_.resolve("123-zzzzzzz") is None and git_.resolve("123-abc12") is None
    assert run(workspace, pins(tmp_path, {"orders": f"123-{sha[:7]}"}, {"orders": "v1.0"})) == 0
    assert "| orders | 123-" in capsys.readouterr().out


def test_a_delta_of_only_merge_commits_falls_back_to_the_first_parent(tmp_path, capsys):
    ws = tmp_path / "ws"
    repo = new_repo(ws)
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "widget work", **{"w.txt": "1\n"})
    git(repo, "tag", "deployed")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "ORD-7 merge the widget", "feature")
    assert run(ws, pins(tmp_path, {"orders": "main"}, {"orders": "deployed"})) == 0
    out = capsys.readouterr().out
    assert "| orders | main | deployed | 1 | 1 | 0 | merge commits used |" in out
    assert "- ORD-7: ORD-7 merge the widget" in out and "Merge commits used" in out


def test_keys_fall_back_to_the_body_skip_placeholders_and_keep_their_order():
    key_re = KEY_RE
    in_body = pdiff.Commit("a" * 40, "tidy the totals", "Refs: ORD-9")
    placeholder = pdiff.Commit("b" * 40, "ORD-0 quick fix", "Real ticket: ORD-11")
    ordered = pdiff.Commit("c" * 40, "ORD-5 and ORD-2, again ORD-5")
    assert pdiff.commit_keys(in_body, key_re) == ["ORD-9"]
    assert pdiff.commit_keys(placeholder, key_re) == ["ORD-0"]
    assert pdiff.commit_keys(placeholder, key_re, frozenset({"ORD-0"})) == ["ORD-11"]
    assert pdiff.commit_keys(ordered, key_re) == ["ORD-5", "ORD-2"]
    # newest first in, keys out in order of first appearance (oldest commit first)
    keyed, _, _ = pdiff.split_commits([ordered, in_body], key_re, [], frozenset())
    assert list(keyed) == ["ORD-9", "ORD-5", "ORD-2"]


def test_ignore_key_on_the_command_line(tmp_path, capsys):
    ws = tmp_path / "ws"
    repo = new_repo(ws)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Dev",
            "-c",
            "user.email=dev@example.test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "ORD-0 stub",
            "-m",
            "Closes ORD-31",
        ],
        check=True,
    )
    commit(repo, "ORD-0 another stub")
    manifests = pins(tmp_path, {"orders": "main"}, {"orders": "v1.0"})
    assert run(ws, manifests, "--keys") == 0
    assert capsys.readouterr().out == "ORD-0\n"
    assert run(ws, manifests, "--keys", "--ignore-key", "ORD-0,ORD-00") == 0
    assert capsys.readouterr().out == "ORD-31\n"


def test_subjects_compare_without_the_pull_request_suffix_case_or_spacing(tmp_path, capsys):
    suffix = re.compile(pdiff.DEFAULT_PR_SUFFIX, re.IGNORECASE)
    assert pdiff.normalise_subject("Fix  the Totals (#12)", suffix) == "fix the totals"
    assert pdiff.normalise_subject("fix the totals (pull request #4) ", suffix) == "fix the totals"
    assert pdiff.normalise_subject("fix (#12) the totals", suffix) == "fix (#12) the totals"
    ws = tmp_path / "ws"
    repo = new_repo(ws)
    commit(repo, "fix the totals", **{"t.txt": "a\n"})
    git(repo, "checkout", "-q", "-b", "hotfix", "v1.0")
    commit(repo, "Fix  the TOTALS (pull request #3)", **{"t.txt": "b\n"})
    git(repo, "checkout", "-q", "main")
    assert run(ws, pins(tmp_path, {"orders": "main"}, {"orders": "hotfix"})) == 0
    out = capsys.readouterr().out
    assert ": same subject" in out and "Would regress" not in out
    custom = ["--pr-suffix-pattern", r"\s*\[nothing\]$"]
    assert run(ws, pins(tmp_path, {"orders": "main"}, {"orders": "hotfix"}), *custom) == 0
    assert "Would regress 1 commit(s)" in capsys.readouterr().out


@pytest.fixture
def fixed_in_repo(tmp_path: Path) -> Path:
    """A release line with two hotfixes; main picks both up, in two different tags."""
    ws = tmp_path / "ws"
    repo = new_repo(ws)
    git(repo, "checkout", "-q", "-b", "release", "v1.0")
    guard = commit(repo, "BUG-5 add the guard", **{"g.txt": "guard\n"})
    commit(repo, "BUG-6 retry the call (#9)", **{"r.txt": "retry-a\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, "feature one", **{"f.txt": "1\n"})
    git(repo, "tag", "candidate")
    git(repo, "cherry-pick", guard)
    git(repo, "tag", "v1.2.0")
    commit(repo, "bug-6 retry the call", **{"r.txt": "retry-b\n"})
    git(repo, "tag", "v1.2.0-hotfix-1")
    commit(repo, "more work", **{"m.txt": "1\n"})
    git(repo, "tag", "v1.3.0")
    return ws


def test_fixed_in_is_the_lowest_tag_carrying_every_regression(fixed_in_repo, tmp_path, capsys):
    manifests = pins(tmp_path, {"orders": "candidate"}, {"orders": "release"})
    assert run(fixed_in_repo, manifests) == 0
    out = capsys.readouterr().out
    assert "**Would regress 2 commit(s) only production has:**" in out
    # v1.2.0 carries the guard but not the retry; the hotfix tag carries both
    assert "Fixed in v1.2.0-hotfix-1: it carries an equivalent of every one." in out


def test_no_tag_carries_every_regression(workspace, manifests, capsys):
    run(workspace, manifests)
    assert "No released tag carries all of them." in capsys.readouterr().out


def test_fixed_in_names_its_default_branch_or_says_it_was_not_checked(
    fixed_in_repo, tmp_path, capsys
):
    manifests = pins(tmp_path, {"orders": "candidate"}, {"orders": "release"})
    run(fixed_in_repo, manifests, "--default-branch", "candidate")
    assert "No released tag carries all of them." in capsys.readouterr().out
    run(fixed_in_repo, manifests, "--default-branch", "nowhere")
    assert "Fixed-in not checked: no default branch found." in capsys.readouterr().out


def test_semver_puts_a_suffixed_tag_after_its_base():
    tags = ["v1.10.0", "v1.2.0-hotfix-2", "v1.2.0", "v1.2.0-hotfix-10", "1.2.0-hotfix-1", "v1.9"]
    assert sorted(tags, key=pdiff.semver) == [
        "v1.2.0",
        "1.2.0-hotfix-1",
        "v1.2.0-hotfix-2",
        "v1.2.0-hotfix-10",
        "v1.9",
        "v1.10.0",
    ]


def test_target_divergence_with_no_net_content_regresses_nothing(tmp_path, capsys):
    ws = tmp_path / "ws"
    repo = new_repo(ws)
    commit(repo, "ORD-1 new report", **{"a.txt": "main\n"})
    git(repo, "checkout", "-q", "-b", "hotfix", "v1.0")
    tweak = commit(repo, "tweak a", **{"a.txt": "hot\n"})
    git(repo, "revert", "--no-edit", tweak)
    git(repo, "checkout", "-q", "main")
    assert run(ws, pins(tmp_path, {"orders": "main"}, {"orders": "hotfix"}), "--strict") == 0
    out = capsys.readouterr().out
    assert "| orders | main | hotfix | 1 | 1 | 0 | net zero |" in out
    assert "no net content (net zero, nothing regresses):" in out and "- tweak a (" in out


def test_a_promotion_onto_a_newer_target_is_a_downgrade(workspace, tmp_path, capsys):
    manifests = pins(tmp_path, {"orders": "v1.0"}, {"orders": "v1.1"})
    assert run(workspace, manifests) == 0
    out = capsys.readouterr().out
    assert "| orders | v1.0 | v1.1 | 0 | 0 | 5 | downgrade |" in out
    assert "**Target is ahead: promotion is a downgrade**" in out
    assert run(workspace, manifests, "--strict") == 2
    capsys.readouterr()


SETTINGS_FROM = """# staging
image = orders:1.4.2
TIMEOUT=30
feature.enabled = true
shared   value = 1   # trailing note
"""
SETTINGS_TO = """image  =  orders:1.4.1
// production
TIMEOUT=60
feature.enabled = false
feature.enabled = false
shared value = 1
"""


@pytest.fixture
def settings_manifests(tmp_path: Path) -> tuple[Path, Path]:
    for env, text in (("staging", SETTINGS_FROM), ("production", SETTINGS_TO)):
        folder = tmp_path / "settings" / env
        folder.mkdir(parents=True)
        (folder / "orders.env").write_text(text)
    template = "settings/{env}/{service}.env"
    source = write(
        tmp_path / "staging.json", {"settings": template, "services": {"orders": "v1.1"}}
    )
    target = write(
        tmp_path / "production.json",
        {"settings": template, "services": {"orders": "v1.0", "billing": "v3.0"}},
    )
    return source, target


def hazards(tmp_path: Path, entries) -> Path:
    return write(tmp_path / "hazards.json", entries)


def test_settings_drift_is_a_multiset_diff_of_normalised_lines(
    workspace, settings_manifests, tmp_path, capsys
):
    file = hazards(
        tmp_path,
        [
            {"pattern": "enabled = false", "side": "to", "message": "switched off"},
            {"pattern": "TIMEOUT", "side": "either", "message": "timeouts differ"},
            {"pattern": "enabled = true", "side": "to", "message": "never on this side"},
        ],
    )
    assert run(workspace, settings_manifests, "--settings-hazards", str(file)) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "Settings drift (3 target-only, 2 source-only):" in out
    assert out.count("- target only: `feature.enabled = false`") == 2
    assert "- source only: `TIMEOUT=30`" in out and "image" not in out.split("## orders")[1]
    assert out.count("**warning:** switched off") == 2
    assert out.count("**warning:** timeouts differ") == 2 and "never on this side" not in out
    assert "| orders | v1.1 | v1.0 | 5 | 3 | 0 | settings drift |" in out
    assert "warning: orders settings: switched off" in captured.err
    assert "## billing" in out and "Settings not compared: no settings file named for FROM" in out


def test_settings_masks_and_missing_files(workspace, settings_manifests, tmp_path, capsys):
    masks = ["--settings-mask", r"orders:\S+", "--settings-mask", r"\d+"]
    run(workspace, settings_manifests, *masks)
    out = capsys.readouterr().out
    assert "TIMEOUT" not in out and "Settings drift (2 target-only, 1 source-only):" in out
    (tmp_path / "settings" / "production" / "orders.env").unlink()
    run(workspace, settings_manifests)
    assert "Settings not compared: TO settings file not found:" in capsys.readouterr().out


def test_the_default_hazard_file_loads_and_bad_ones_exit_one(
    workspace, manifests, tmp_path, capsys
):
    assert pdiff.load_hazards(pdiff.DEFAULT_HAZARDS_FILE)
    for entries in (
        {"hazards": "nope"},
        [{"side": "to"}],
        [{"pattern": "x", "side": "sideways"}],
        [{"pattern": "(", "side": "to"}],
    ):
        file = hazards(tmp_path, entries)
        assert run(workspace, manifests, "--settings-hazards", str(file)) == 1
    (tmp_path / "hazards.json").write_text("{not json")
    assert run(workspace, manifests, "--settings-hazards", str(tmp_path / "hazards.json")) == 1
    source, _ = manifests
    write(source, {"settings": 3, "services": {"orders": "v1.1"}})
    assert run(workspace, manifests) == 1
    write(source, {"settings": "{nope}.env", "services": {"orders": "v1.1"}})
    assert run(workspace, manifests) == 1
    assert "bad settings template" in capsys.readouterr().err


def test_json_output_matches_the_schema(workspace, settings_manifests, tmp_path, capsys):
    from traceability._util import validate

    assert run(workspace, settings_manifests, "--json") == 1
    assert "--json needs --candidate-id" in capsys.readouterr().err
    tests = tmp_path / "suite"
    tests.mkdir()
    (tests / "test_totals.py").write_text("# ORD-42\n")
    report = tmp_path / "diff.json"
    args = ["--json", "--candidate-id", "rc-7", "--test-root", str(tests), "-o", str(report)]
    assert run(workspace, settings_manifests, *args) == 0
    doc = json.loads(report.read_text())
    validate(doc, "promotion-diff.schema.json")
    assert (doc["candidateId"], doc["from"], doc["to"]) == ("rc-7", "staging", "production")
    assert doc["pins"] == {"from": {"orders": "v1.1"}, "to": {"billing": "v3.0", "orders": "v1.0"}}
    services = {s["service"]: s for s in doc["services"]}
    assert services["billing"]["notCompared"].startswith("not in FROM")
    orders = services["orders"]
    assert [f["key"] for f in orders["functional"]] == ["BUG-142", "ORD-42", "ORD-43"]
    assert orders["functional"][1]["tests"] == [(tests / "test_totals.py").as_posix()]
    assert orders["functional"][2]["tests"] == []
    assert orders["regressing"] == [] and orders["fixedIn"] is None
    assert orders["noise"] == 1 and len(orders["unkeyed"]) == 2
    drift = {d["service"]: d for d in doc["settingsDrift"]}
    assert drift["orders"]["toOnly"].count("feature.enabled = false") == 2
    assert drift["billing"]["notCompared"]


def test_json_regressions_carry_fixed_in(fixed_in_repo, tmp_path, capsys):
    from traceability._util import validate

    manifests = pins(tmp_path, {"orders": "candidate"}, {"orders": "release"})
    assert run(fixed_in_repo, manifests, "--json", "--candidate-id", "rc-1", "--strict") == 2
    doc = json.loads(capsys.readouterr().out)
    validate(doc, "promotion-diff.schema.json")
    (orders,) = doc["services"]
    assert orders["fixedIn"] == "v1.2.0-hotfix-1" and orders["fixedInChecked"] is True
    assert [r["subject"] for r in orders["regressing"]] == [
        "BUG-5 add the guard",
        "BUG-6 retry the call (#9)",
    ]
    assert orders["regressing"][0]["fixedTags"] == ["v1.2.0", "v1.2.0-hotfix-1", "v1.3.0"]
    assert all(r["equivalent"] for r in orders["regressing"])


@pytest.mark.parametrize(
    ("from_ref", "to_ref", "problem", "expected"),
    [
        ("v1.2.0", "v1.1.0", None, "changed"),
        ("v1.2.0", "v1.2.0", None, "unchanged"),
        (None, "v1.1.0", "not in FROM: the promotion would not touch it", "one-sided"),
        ("v1.2.0", None, "not in TO: new service, every commit up to FROM is new", "one-sided"),
        ("v1.2.0", "v1.1.0", "no git clone at /tmp/missing", "not-compared"),
    ],
)
def test_service_status_is_one_word_per_service(from_ref, to_ref, problem, expected):
    delta = pdiff.ServiceDelta(service="billing", from_ref=from_ref, to_ref=to_ref, problem=problem)
    assert pdiff.service_status(delta) == expected
