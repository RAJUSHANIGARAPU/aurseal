"""The ``testkeel`` CLI: new-assignment, list-types and assignment check.

Rendering uses a minimal stand-in template (``fixtures/template``) with the same layout as
the real ones, so these tests do not depend on the real templates' content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testkeel.assignment import scaffold
from testkeel.assignment.slug import package_name, slugify
from testkeel.cli import app

TEMPLATE = Path(__file__).parent / "fixtures" / "template"
TERM = "zorblax"
runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch) -> dict[str, str]:
    """An isolated home and cache, and the stand-in template."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(scaffold, "TEMPLATES_ROOT", TEMPLATE)
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

    monkeypatch.setattr(scaffold, "render", broken)
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
