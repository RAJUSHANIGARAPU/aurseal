"""``testkeel assignment update`` end to end against a local template repository.

The fixture builds a git repository holding this installation's templates, tags it as the
installed release, changes the templates in a second tagged commit, and serves both tags
from a bare clone. A project is generated at the first tag with ``--updatable``, edited and
committed like a user would, then updated to the second tag.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("copier", reason="copier is not installed")
templates = pytest.importorskip("testkeel.templates", reason="testkeel is not importable")
if shutil.which("git") is None:
    pytest.skip("git is not on PATH", allow_module_level=True)

from testkeel.assignment import scaffold, update  # noqa: E402
from testkeel.cli import app  # noqa: E402

runner = CliRunner()
V1 = scaffold.TEMPLATE_TAG
V2 = "testkeel-v99.0.0"
GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
       "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]  # fmt: skip

# The deliberate template change between the two tags: a section appended to a file every
# type renders (copied verbatim, no Jinja), and a new file.
EDITORCONFIG = "_base/.editorconfig"
APPENDED = "\n[*.toml]\nindent_size = 2\n"
NEW_FILE = "_base/CONTRIBUTING.md"
NEW_TEXT = "# Contributing\n\nRun `make test` before you push.\n"


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


@pytest.fixture
def source(tmp_path) -> Path:
    """A bare template repository with tags V1 (the installed templates) and V2."""
    repo = tmp_path / "upstream"
    target = repo / update.TEMPLATES_PATH
    shutil.copytree(templates.TEMPLATES_DIR, target, ignore=shutil.ignore_patterns("__pycache__"))
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "templates")
    git(repo, "tag", V1)
    with (target / EDITORCONFIG).open("a", encoding="utf-8") as fh:
        fh.write(APPENDED)
    (target / NEW_FILE).write_text(NEW_TEXT, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "template change")
    git(repo, "tag", V2)
    bare = tmp_path / "upstream.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(bare))
    return bare


@pytest.fixture
def env(tmp_path, monkeypatch) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    return {"HOME": str(home), "XDG_CACHE_HOME": str(tmp_path / "cache"), "LEAK_DENYLIST": ""}


def generate(env, type_: str = "strategy", name: str = "Checkout Plan", *extra: str) -> Path:
    result = runner.invoke(
        app,
        ["new-assignment", "--non-interactive", "--type", type_, "--name", name,
         "--updatable", "--no-lock", *extra],
        env=env,
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    project = Path.cwd() / scaffold.slugify(name)
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "generated")
    return project


def commit_edit(project: Path, name: str, old: str, new: str) -> None:
    path = project / name
    text = path.read_text("utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    git(project, "commit", "-q", "-am", "my edit")


def run_update(env, project: Path, source: Path, *extra: str):
    return runner.invoke(
        app, ["assignment", "update", str(project), "--to", V2, "--source", str(source), *extra],
        env=env,
    )  # fmt: skip


def test_the_template_change_arrives_and_the_user_edit_survives(env, source):
    project = generate(env)
    assert f"_commit: {V1}" in (project / scaffold.ANSWERS_FILE).read_text("utf-8")
    # The user's edit is in the same file as the template change, a few lines above it.
    commit_edit(project, ".editorconfig", "indent_size = 4", "indent_size = 3")
    (project / "notes.md").write_text("mine\n", encoding="utf-8")
    git(project, "add", "notes.md")
    git(project, "commit", "-q", "-m", "notes")

    result = run_update(env, project, source)

    assert result.exit_code == update.OK, result.output
    assert f"from {V1} to {V2}" in result.output
    editorconfig = (project / ".editorconfig").read_text("utf-8")
    assert "indent_size = 3" in editorconfig and "indent_size = 4" not in editorconfig
    assert editorconfig.endswith(APPENDED)
    assert (project / "CONTRIBUTING.md").read_text("utf-8") == NEW_TEXT
    assert (project / "notes.md").read_text("utf-8") == "mine\n"
    assert f"_commit: {V2}" in (project / scaffold.ANSWERS_FILE).read_text("utf-8")
    changed = git(project, "status", "--porcelain").splitlines()
    assert sorted(line[3:] for line in changed) == [
        ".copier-answers.yml", ".editorconfig", "CONTRIBUTING.md",
    ]  # fmt: skip
    assert "<<<<<<<" not in editorconfig


def test_a_composed_type_replays_every_pass(env, source):
    """fullstack renders base plus three overlays; only the template change may differ."""
    project = generate(
        env, "fullstack", "Shop Stack",
        "--target-url", "https://shop.example.test", "--api-base-url", "https://api.example.test",
    )  # fmt: skip
    result = run_update(env, project, source)
    assert result.exit_code == update.OK, result.output
    changed = git(project, "status", "--porcelain").splitlines()
    assert sorted(line[3:] for line in changed) == [
        ".copier-answers.yml", ".editorconfig", "CONTRIBUTING.md",
    ]  # fmt: skip


def test_a_conflict_is_reported_and_marked(env, source):
    project = generate(env)
    # The user appended to the end of the file, exactly where the template change lands.
    path = project / ".editorconfig"
    path.write_text(path.read_text("utf-8") + "\n[*.sh]\nindent_size = 2\n", encoding="utf-8")
    git(project, "commit", "-q", "-am", "my edit")

    result = run_update(env, project, source)

    assert result.exit_code == update.CONFLICT, result.output
    assert "CONFLICT  .editorconfig" in result.output
    assert "1 conflict(s)" in result.output
    text = path.read_text("utf-8")
    assert "<<<<<<< yours" in text and f">>>>>>> {V2}" in text
    assert "[*.sh]" in text and "[*.toml]" in text
    # The parts without a conflict still applied.
    assert (project / "CONTRIBUTING.md").read_text("utf-8") == NEW_TEXT


def test_uncommitted_changes_stop_the_update(env, source):
    project = generate(env)
    (project / "README.md").write_text("draft\n", encoding="utf-8")
    result = run_update(env, project, source)
    assert result.exit_code == scaffold.USAGE
    assert "uncommitted changes" in result.output
    assert (project / "README.md").read_text("utf-8") == "draft\n"


def test_a_project_without_answers_is_not_updatable(env, source, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_update(env, plain, source)
    assert result.exit_code == scaffold.USAGE
    assert "--updatable" in result.output


def test_already_at_the_target_is_a_no_op(env, source):
    project = generate(env)
    result = runner.invoke(
        app, ["assignment", "update", str(project), "--to", V1, "--source", str(source)], env=env
    )
    assert result.exit_code == update.OK
    assert "already at" in result.output


def test_an_unknown_tag_is_reported(env, source):
    project = generate(env)
    result = runner.invoke(
        app,
        ["assignment", "update", str(project), "--to", "testkeel-v0.0.0", "--source",
         str(source)],
        env=env,
    )  # fmt: skip
    assert result.exit_code == scaffold.PREREQUISITE
    assert "cannot fetch tag testkeel-v0.0.0" in result.output


def test_answers_are_read_as_written(tmp_path):
    text = (
        "# comment\n_commit: testkeel-v0.1.0\n_src_path: https://example.test/repo\n"
        'docker: true\nproject_name: "ACME: Shop"\npython_version: "3.12"\n'
    )
    assert update.read_answers(text) == {
        "_commit": "testkeel-v0.1.0",
        "_src_path": "https://example.test/repo",
        "docker": True,
        "project_name": "ACME: Shop",
        "python_version": "3.12",
    }


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    return root


def test_every_case_of_the_file_by_file_merge(tmp_path):
    old = _tree(tmp_path / "old", {
        "same.txt": b"a\n", "gone.txt": b"x\n", "gone-edited.txt": b"x\n",
        "deleted-by-you.txt": b"1\n", "bin.dat": b"\x00old", "sub/only.txt": b"o\n",
    })  # fmt: skip
    new = _tree(tmp_path / "new", {
        "same.txt": b"a\n", "deleted-by-you.txt": b"2\n", "bin.dat": b"\x00new",
        "fresh.txt": b"f\n",
    })  # fmt: skip
    project = _tree(tmp_path / "project", {
        "same.txt": b"edited\n", "gone.txt": b"x\n", "gone-edited.txt": b"mine\n",
        "bin.dat": b"\x00mine", "sub/only.txt": b"o\n", "own.txt": b"own\n",
    })  # fmt: skip
    work = tmp_path / "work"
    work.mkdir()

    result = update.apply(project, old, new, ("v1", "v2"), work)

    assert result.code == update.CONFLICT
    assert result.lines == [
        "  CONFLICT  bin.dat (cannot be merged as text; kept yours)",
        "  skipped   deleted-by-you.txt (you deleted it; v2 changes it)",
        "  added     fresh.txt",
        "  kept      gone-edited.txt (removed by v2, but you changed it)",
        "  removed   gone.txt",
        "  removed   sub/only.txt",
    ]
    assert (project / "same.txt").read_bytes() == b"edited\n"
    assert (project / "bin.dat").read_bytes() == b"\x00mine"
    assert not (project / "deleted-by-you.txt").exists()
    assert not (project / "gone.txt").exists() and not (project / "sub").exists()
    assert (project / "gone-edited.txt").read_bytes() == b"mine\n"
    assert (project / "own.txt").read_bytes() == b"own\n"
