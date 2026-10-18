"""The ``testkeel`` CLI: new-assignment, list-types and assignment check.

Rendering uses a minimal stand-in template (``fixtures/template``) with the same layout as
the real ones, so these tests do not depend on the real templates' content.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testkeel.assignment import scaffold
from testkeel.assignment.slug import package_name, slugify
from testkeel.cli import app

TEMPLATE = Path(__file__).parent / "fixtures" / "template"


def fixture_render(type_, destination, data):
    """The packaged renderer's algorithm over the stand-in template: base, then overlay."""
    from copier import run_copy

    for layer in ("base", "overlay"):
        run_copy(
            str(TEMPLATE),
            str(destination),
            data={**data, "type": type_, "layer": layer},
            defaults=True,
            overwrite=True,
            quiet=True,
        )


TERM = "zorblax"
runner = CliRunner()
REAL_LOCK = scaffold.lock
REAL_RENDER = scaffold.render
LOCKED: list[Path] = []


def fake_lock(staging, _environ):
    """Stands in for ``uv lock`` (network); the real one is tested on its own below."""
    LOCKED.append(staging)
    return "lock: (test stand-in)"


@pytest.fixture
def env(tmp_path, monkeypatch) -> dict[str, str]:
    """An isolated home and cache, the stand-in template and a stand-in lock step."""
    home = tmp_path / "home"
    home.mkdir()
    LOCKED.clear()
    monkeypatch.setattr(scaffold, "render", fixture_render)
    monkeypatch.setattr(scaffold, "lock", fake_lock)
    monkeypatch.chdir(tmp_path)
    return {"HOME": str(home), "XDG_CACHE_HOME": str(tmp_path / "cache"), "LEAK_DENYLIST": ""}


def _staging(tmp_path: Path) -> list[Path]:
    root = tmp_path / "cache" / "testkeel" / "staging"
    return sorted(root.iterdir()) if root.is_dir() else []


def _new(env, *args: str):
    return runner.invoke(app, ["new-assignment", "--non-interactive", "--no-git", *args], env=env)


# ------------------------------------------------------------------ slugging


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Café Checkout", "cafe-checkout"),
        ("acme / checkout!!", "acme-checkout"),
        ("2026 task", "qa-2026-task"),
        ("  --Shop--  ", "shop"),
        ("!!!", ""),
        ("a" * 30 + " " + "b" * 30, "a" * 30),
    ],
)
def test_slugging(name, slug):
    assert slugify(name) == slug
    assert len(slugify(name)) <= 48


@pytest.mark.parametrize(
    ("slug", "package"), [("http", "http_qa"), ("pytest", "pytest_qa"), ("acme-shop", "acme_shop")]
)
def test_package_names_never_shadow_a_module(slug, package):
    assert package_name(slug) == package


# ------------------------------------------------------------------ list-types


def test_list_types_shows_available_and_planned():
    result = runner.invoke(app, ["list-types", "--json"])
    assert result.exit_code == 0
    status = {t["name"]: t["status"] for t in json.loads(result.output)}
    assert {n for n, s in status.items() if s == "available"} == {"web-ui", "api-rest", "strategy"}
    assert {n for n, s in status.items() if s == "planned"} == {
        "api-graphql",
        "perf-k6",
        "perf-locust",
        "a11y",
        "mobile",
        "fullstack",
    }
    plain = runner.invoke(app, ["list-types"])
    assert "perf-k6" in plain.output and "planned" in plain.output


# ------------------------------------------------------------------ new-assignment


def test_a_project_is_rendered_gated_and_moved(env, tmp_path):
    task = tmp_path / "brief.md"
    task.write_text("# Checkout flow for ACME Shop\n\nTest the cart.\n", "utf-8")
    result = _new(
        env,
        "--type", "web-ui", "--name", "ACME Checkout", "--company", "ACME Shop",
        "--task-file", str(task), "--target-url", "https://shop.example.test",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    project = tmp_path / "acme-checkout"
    readme = (project / "README.md").read_text("utf-8")
    assert "# ACME Checkout" in readme and "ACME Shop" in readme
    assert "Checkout flow for ACME Shop" in readme
    assert 'BASE_URL = "https://shop.example.test"' in (project / "tests/test_smoke.py").read_text()
    assert (project / "Dockerfile").is_file()
    assert (project / ".task" / "TASK.md").is_file()
    assert ".task/" in (project / ".gitignore").read_text("utf-8")
    assert not (project / ".copier-answers.yml").exists()
    assert not (project / ".git").exists()
    assert _staging(tmp_path) == []
    assert "Created acme-checkout (web-ui)" in result.output


def test_flags_reach_the_template(env, tmp_path):
    result = _new(
        env, "--type", "api-rest", "--name", "Booking API", "--no-docker", "--python", "3.11",
        "--output-dir", str(tmp_path / "out"),
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    assert not (out / "Dockerfile").exists()
    assert ">=3.11" in (out / "pyproject.toml").read_text("utf-8")
    assert "https://restful-booker.herokuapp.com" in (out / "tests/test_smoke.py").read_text()


def test_git_init_makes_no_commit(env, tmp_path):
    result = runner.invoke(
        app,
        ["new-assignment", "--non-interactive", "--type", "strategy", "--name", "Plan"],
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "plan" / ".git").is_dir()
    assert not (tmp_path / "plan" / ".git" / "refs" / "heads" / "main").exists()


def _git_identity(env, tmp_path, email: str) -> dict[str, str | None]:
    """A global git config of our own; the machine's identity never leaks into the test."""
    config = tmp_path / "gitconfig"
    config.write_text(f"[user]\n\tname = Jo Tester\n\temail = {email}\n", "utf-8")
    unset = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
             "GIT_COMMITTER_EMAIL", "EMAIL", "GIT_DIR", "GIT_WORK_TREE")  # fmt: skip
    return {**env, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
            **dict.fromkeys(unset)}  # fmt: skip


def _init(env):
    return runner.invoke(
        app, ["new-assignment", "--non-interactive", "--type", "strategy", "--name", "Plan"],
        env=env,
    )  # fmt: skip


def test_git_prints_the_identity_it_will_use(env, tmp_path):
    env = _git_identity(env, tmp_path, "jo@mail.example.test")
    result = _init({**env, "LEAK_DENYLIST": f"term:{TERM}\n"})
    assert result.exit_code == 0, result.output
    assert "commits will be authored as Jo Tester <jo@mail.example.test>" in result.output
    assert "denylist" not in result.output.replace("the denylist is", "")


def test_a_denylisted_git_identity_is_warned_about_without_naming_it(env, tmp_path):
    env = _git_identity(env, tmp_path, f"jo@{TERM}.test")
    result = _init({**env, "LEAK_DENYLIST": f"term:{TERM}\n"})
    assert result.exit_code == 0, result.output
    assert "your git identity matches a denylist entry" in result.output
    assert TERM not in result.output.lower()
    assert "Jo Tester" not in result.output
    git_config = (tmp_path / "plan" / ".git" / "config").read_text("utf-8")
    assert "[user]" not in git_config and TERM not in git_config
    assert not (tmp_path / "plan" / ".git" / "refs" / "heads" / "main").exists()


def test_dry_run_writes_nothing(env, tmp_path):
    result = _new(env, "--type", "strategy", "--name", "Plan", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "TEST_PLAN.md" in result.output
    assert not (tmp_path / "plan").exists()
    assert _staging(tmp_path) == []


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["--type", "perf-k6", "--name", "x"], 2, "planned"),
        (["--type", "nope", "--name", "x"], 2, "unknown type"),
        (["--type", "web-ui"], 2, "missing --name"),
        (["--name", "x"], 2, "missing --type"),
        (["--type", "web-ui", "--name", "!!!"], 2, "no letters"),
        (["--type", "web-ui", "--name", "x", "--target-url", "ftp://x"], 2, "http(s) URL"),
        (["--type", "web-ui", "--name", "x", "--python", "3.9"], 2, "--python"),
        (["--type", "web-ui", "--name", "x", "--ci", "gitlab"], 2, "--ci"),
        (["--type", "web-ui", "--name", "x", "--task-file", "missing.md"], 2, "no such file"),
    ],
)
def test_usage_errors_exit_2_and_write_nothing(env, tmp_path, args, code, message):
    result = _new(env, *args)
    assert result.exit_code == code
    assert message in result.output
    assert _staging(tmp_path) == []
    assert not (tmp_path / "x").exists()


def test_a_non_empty_output_dir_needs_force(env, tmp_path):
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "keep.txt").write_text("mine", "utf-8")
    assert _new(env, "--type", "strategy", "--name", "Plan").exit_code == 3
    forced = _new(env, "--type", "strategy", "--name", "Plan", "--force")
    assert forced.exit_code == 0, forced.output
    assert (tmp_path / "plan" / "keep.txt").read_text("utf-8") == "mine"
    assert (tmp_path / "plan" / "TEST_PLAN.md").is_file()


def test_a_planted_denylist_term_is_refused_and_nothing_is_written(env, tmp_path):
    env = {**env, "LEAK_DENYLIST": f"term:{TERM}\n"}
    result = _new(env, "--type", "web-ui", "--name", "Shop", "--company", f"{TERM.title()} BV")
    assert result.exit_code == 4
    assert "README.md:3 deny.term [e1]" in result.output
    assert TERM not in result.output.lower()
    assert not (tmp_path / "shop").exists()
    kept = _staging(tmp_path)
    assert len(kept) == 1 and str(kept[0]) in result.output  # kept for inspection


def test_the_slug_is_checked_against_the_denylist(env, tmp_path):
    env = {**env, "LEAK_DENYLIST": f"term:{TERM}\n"}
    result = _new(env, "--type", "strategy", "--name", f"{TERM} take-home", "--dry-run")
    assert result.exit_code == 0  # a dry run does not gate
    result = _new(env, "--type", "strategy", "--name", f"{TERM} take-home")
    assert result.exit_code == 4
    assert "slug:1 deny.term" in result.output and TERM not in result.output.lower()


def test_a_render_failure_cleans_up_staging(env, tmp_path, monkeypatch):
    def broken(*_args):
        raise RuntimeError("template exploded")

    result = scaffold.new_assignment(
        scaffold.Options(type="strategy", name="Plan", git=False),
        staging_root=tmp_path / "cache" / "testkeel" / "staging",
        environ=env,
        renderer=broken,
    )
    assert result.code == 1
    assert "details in" in result.lines[0]
    assert _staging(tmp_path) == []
    assert not (tmp_path / "plan").exists()
    logs = list((tmp_path / "cache" / "testkeel" / "logs").iterdir())
    assert "template exploded" in logs[0].read_text("utf-8")


# ------------------------------------------------------------------ assignment check


def test_assignment_check_exit_codes(env, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("# Clean\n", "utf-8")
    clean = runner.invoke(app, ["assignment", "check", str(project)], env=env)
    assert clean.exit_code == 0 and "warning" in clean.output
    strict = runner.invoke(app, ["assignment", "check", str(project), "--strict"], env=env)
    assert strict.exit_code == 2
    (project / "notes.md").write_text(f"from {TERM}\n", "utf-8")
    env = {**env, "LEAK_DENYLIST": f"term:{TERM}\n"}
    found = runner.invoke(app, ["assignment", "check", str(project), "--json"], env=env)
    assert found.exit_code == 4
    data = json.loads(found.output)
    assert data["findings"] == [
        {"location": "notes.md", "line": 1, "rule": "deny.term", "entry": "e1"}
    ]
    missing = runner.invoke(app, ["assignment", "check", str(tmp_path / "none")], env=env)
    assert missing.exit_code == 2


# ------------------------------------------------------------------ lock and self-check


def _fake_uv(tmp_path: Path, exit_code: int = 0) -> Path:
    """A ``uv`` on PATH that records its arguments and index variables, then writes a lock."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{tmp_path}/uv-args"\n'
        f'env | grep "^UV_" > "{tmp_path}/uv-env" || true\n'
        f"if [ {exit_code} -ne 0 ]; then echo 'resolution failed' >&2; exit {exit_code}; fi\n"
        "printf 'version = 1\\n' > uv.lock\n",
        "utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _project(tmp_path: Path) -> Path:
    staging = tmp_path / "staged"
    staging.mkdir()
    (staging / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', "utf-8")
    return staging


@pytest.mark.skipif(os.name != "posix", reason="the stand-in uv is a shell script")
def test_lock_forces_pypi_and_drops_index_overrides(tmp_path):
    bin_dir = _fake_uv(tmp_path)
    staging = _project(tmp_path)
    environ = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "UV_INDEX_URL": "https://registry.corp.example/simple",
        "UV_EXTRA_INDEX_URL": "https://registry.corp.example/extra",
    }
    note = REAL_LOCK(staging, environ)
    assert note == "lock: uv.lock written (index: PyPI)"
    assert (staging / "uv.lock").is_file()
    args = (tmp_path / "uv-args").read_text("utf-8").split()
    assert args[:4] == ["lock", "--no-config", "--default-index", scaffold.PYPI_SIMPLE]
    assert "corp.example" not in (tmp_path / "uv-env").read_text("utf-8")


def test_lock_without_uv_or_pyproject_is_a_notice(tmp_path, monkeypatch):
    staging = _project(tmp_path)
    monkeypatch.setattr(scaffold.shutil, "which", lambda *_a, **_k: None)
    assert "uv is not on PATH" in REAL_LOCK(staging, {})
    assert not (staging / "uv.lock").exists()
    (staging / "pyproject.toml").unlink()
    assert "nothing to lock" in REAL_LOCK(staging, {})


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_lock_runs_the_real_uv_offline(tmp_path):
    staging = _project(tmp_path)
    environ = {"UV_OFFLINE": "1", "UV_CACHE_DIR": str(tmp_path / "uv-cache")}
    assert "written" in REAL_LOCK(staging, environ)
    assert 'name = "x"' in (staging / "uv.lock").read_text("utf-8")


@pytest.mark.skipif(os.name != "posix", reason="the stand-in uv is a shell script")
def test_a_failing_lock_exits_5_and_keeps_staging(env, tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold, "lock", REAL_LOCK)
    bin_dir = _fake_uv(tmp_path, exit_code=1)
    env = {**env, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    result = _new(env, "--type", "web-ui", "--name", "Shop")
    assert result.exit_code == 5, result.output
    assert "self-check failed at uv lock (exit 1)" in result.output
    assert "resolution failed" in result.output
    assert not (tmp_path / "shop").exists()
    kept = _staging(tmp_path)
    assert len(kept) == 1 and str(kept[0]) in result.output


def test_the_pipeline_locks_unless_told_not_to(env, tmp_path):
    result = _new(env, "--type", "web-ui", "--name", "Shop")
    assert result.exit_code == 0, result.output
    assert len(LOCKED) == 1 and "lock: (test stand-in)" in result.output
    assert "self-check: green" in result.output
    skipped = _new(env, "--type", "web-ui", "--name", "Shop two", "--no-lock")
    assert skipped.exit_code == 0, skipped.output
    assert len(LOCKED) == 1 and "lock: skipped (--no-lock)" in skipped.output


def _render_with(extra: dict[str, str]):
    def render(type_, destination, data):
        fixture_render(type_, destination, data)
        for name, text in extra.items():
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            (destination / name).write_text(text, "utf-8")

    return render


def test_code_that_does_not_compile_fails_the_self_check(env, tmp_path):
    result = scaffold.new_assignment(
        scaffold.Options(type="web-ui", name="Shop", git=False, lock=False),
        staging_root=tmp_path / "cache" / "testkeel" / "staging",
        environ=env,
        renderer=_render_with({"tests/test_broken.py": "def broken(:\n"}),
    )
    assert result.code == 5
    text = "\n".join(result.lines)
    assert "self-check failed at compile" in text
    assert "tests/test_broken.py:1: SyntaxError" in text
    assert not (tmp_path / "shop").exists()
    assert result.staging is not None and result.staging.is_dir()
    assert not list(result.staging.rglob("__pycache__"))


@pytest.mark.skipif(scaffold._ruff_command() is None, reason="ruff is not installed")
def test_lint_errors_fail_the_self_check(env, tmp_path):
    result = scaffold.new_assignment(
        scaffold.Options(type="web-ui", name="Shop", git=False, lock=False),
        staging_root=tmp_path / "cache" / "testkeel" / "staging",
        environ=env,
        renderer=_render_with({"tests/test_lint.py": "import os\n"}),
    )
    assert result.code == 5
    text = "\n".join(result.lines)
    assert "self-check failed at ruff check" in text and "F401" in text
    assert result.staging is not None
    assert not (result.staging / ".ruff_cache").exists()
    assert not (tmp_path / "shop").exists()


def test_without_ruff_the_self_check_compiles_only(tmp_path, monkeypatch):
    staging = tmp_path / "staged"
    (staging / "tests").mkdir(parents=True)
    (staging / "tests" / "test_x.py").write_text("import os\n", "utf-8")
    monkeypatch.setattr(scaffold, "_ruff_command", lambda: None)
    assert scaffold.self_check(staging) == [
        "self-check: ruff is not available; lint skipped (compile only)"
    ]


# ------------------------------------------------------------------ --updatable


def test_projects_are_not_updatable_by_default(env, tmp_path):
    result = _new(env, "--type", "api-rest", "--name", "Booking")
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "booking" / ".copier-answers.yml").exists()


def test_updatable_records_the_public_source_at_the_release_tag(env, tmp_path, monkeypatch):
    yaml = pytest.importorskip("yaml")
    from testkeel import __version__
    from testkeel.leakgate.project import PUBLIC_SOURCE

    monkeypatch.setattr(scaffold, "render", REAL_RENDER)  # the packaged templates
    result = _new(env, "--type", "api-rest", "--name", "Booking API", "--updatable", "--no-lock")
    assert result.exit_code == 0, result.output
    text = (tmp_path / "booking-api" / ".copier-answers.yml").read_text("utf-8")
    answers = yaml.safe_load(text)
    assert (
        answers["_src_path"] == PUBLIC_SOURCE == "https://github.com/RAJUSHANIGARAPU/wiki_project"
    )
    assert answers["_commit"] == f"testkeel-v{__version__}"
    assert answers["type"] == "api-rest"
    assert answers["project_slug"] == "booking-api"
    assert answers["updatable"] is True and answers["docker"] is True
    assert answers["api_base_url"] == "https://restful-booker.herokuapp.com"
    assert str(Path.home()) not in text and "site-packages" not in text
