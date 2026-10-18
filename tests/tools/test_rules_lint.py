"""Lint for path-scoped assistant rules (tools.rules_lint)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.rules_lint import glob_regex, lint_rules, main

ROOT = Path(__file__).resolve().parents[2]
FILES = ["ui/pages/search_page.py", "ui/tests/test_search.py", "gate/lanes.py"]


def _rule(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body)


def test_glob_semantics():
    assert glob_regex("ui/**").match("ui/pages/search_page.py")
    assert glob_regex("**/test_*.py").match("ui/tests/test_search.py")
    assert glob_regex("gate/*.py").match("gate/lanes.py")
    assert not glob_regex("gate/*.py").match("gate/sub/lanes.py")


def test_a_good_rule_is_clean(tmp_path):
    _rule(tmp_path, "pages.md", "---\npaths:\n  - 'ui/pages/**'\n---\nUse page objects.\n")
    assert lint_rules(tmp_path, FILES, "see pages.md") == []


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("No front matter.\n", "no YAML front matter"),
        ("---\ntitle: x\n---\n", "non-empty 'paths' list"),
        ("---\npaths: []\n---\n", "non-empty 'paths' list"),
        ("---\npaths: [3]\n---\n", "is not a glob string"),
        ("---\npaths: ['nowhere/**']\n---\n", "matches no tracked file"),
    ],
)
def test_each_rule_can_fail(tmp_path, body, problem):
    _rule(tmp_path, "x.md", body)
    findings = lint_rules(tmp_path, FILES, "x.md")
    assert any(problem in f for f in findings), findings


def test_a_rule_the_guide_does_not_name_is_a_finding(tmp_path):
    _rule(tmp_path, "pages.md", "---\npaths: ['ui/pages/**']\n---\n")
    assert lint_rules(tmp_path, FILES, "no mention") == ["pages.md: not named in the project guide"]


def test_this_repository_rules():
    rules = ROOT / ".claude" / "rules"
    if not any(rules.glob("*.md")):
        pytest.skip("no .claude/rules/*.md in this checkout yet")
    assert main(["--dir", str(rules), "--guide", str(ROOT / "CLAUDE.md")]) == 0
