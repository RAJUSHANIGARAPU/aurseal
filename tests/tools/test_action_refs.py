"""tools.action_refs: finds every action reference and fails on one that does not exist."""

from __future__ import annotations

from pathlib import Path

from tools import action_refs


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_references_cover_workflows_templates_and_examples(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml", "steps:\n  - uses: actions/checkout@v4\n")
    part = "src/testkeel/templates/x/ci-test-job.part"
    _write(tmp_path, part, "- uses: acme/setup-thing@v2.1.0\n")
    _write(tmp_path, "examples/e/.github/workflows/ci.yml", "uses: 'acme/tools/sub/dir@main'\n")
    _write(tmp_path, ".github/workflows/local.yml", "uses: ./.github/actions/local\n")

    assert action_refs.references(tmp_path) == {
        ("actions/checkout", "v4"),
        ("acme/setup-thing", "v2.1.0"),
        ("acme/tools", "main"),
    }


def test_a_ref_that_does_not_exist_fails(tmp_path: Path, capsys) -> None:
    workflow = "- uses: acme/setup-thing@v10\n- uses: acme/ok@v1\n"
    _write(tmp_path, ".github/workflows/ci.yml", workflow)
    existing = {"repos/acme/ok/git/ref/tags/v1"}

    assert action_refs.main([str(tmp_path)], fetch=lambda path: path in existing) == 1
    assert "does not exist: acme/setup-thing@v10" in capsys.readouterr().out


def test_a_branch_and_a_commit_resolve(tmp_path: Path) -> None:
    sha = "a" * 40
    _write(tmp_path, ".github/workflows/ci.yml", f"- uses: acme/a@main\n- uses: acme/b@{sha}\n")
    existing = {"repos/acme/a/git/ref/heads/main", f"repos/acme/b/commits/{sha}"}

    assert action_refs.main([str(tmp_path)], fetch=lambda path: path in existing) == 0


def test_an_unreachable_api_is_not_a_pass(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/ci.yml", "- uses: acme/a@v1\n")

    assert action_refs.main([str(tmp_path)], fetch=lambda path: None) == 2
