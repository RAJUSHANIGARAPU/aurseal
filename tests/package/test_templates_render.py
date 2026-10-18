"""Render every assignment template and check what comes out.

Each case renders base + overlay through ``testkeel.templates.render`` into a temporary
directory, then checks the output the way a reviewer of a generated project would:
the file tree matches the committed snapshot, no template syntax survived, nothing
identifies the tool unless asked to, and the code lints, compiles and (for api-rest)
passes its own offline tests.

The snapshot lists every rendered file with a hash of its content. An intended template
change updates it in the same commit: ``UPDATE_TEMPLATE_SNAPSHOTS=1 pytest`` on this file.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("copier", reason="copier is not installed")
templates = pytest.importorskip("testkeel.templates", reason="testkeel is not importable")

SNAPSHOT = Path(__file__).with_name("template_snapshots.json")
UPDATE = os.environ.get("UPDATE_TEMPLATE_SNAPSHOTS") == "1"

COMMON = {
    "project_name": "ACME Shop",
    "company": "ACME",
    "task_summary": "Cover checkout with automated tests.",
    "target_url": "https://shop.example.test",
    "api_base_url": "https://api.example.test",
}
CASES = {
    "web-ui": ("web-ui", {}),
    "web-ui-allure-no-docker": ("web-ui", {"report": "allure", "docker": False}),
    "api-rest": ("api-rest", {}),
    "api-rest-both-no-ci-py311": (
        "api-rest",
        {"report": "both", "ci": "none", "python_version": "3.11", "credit": True},
    ),
    "strategy": ("strategy", {}),
    "strategy-no-ci": ("strategy", {"ci": "none"}),
}
CODE_CASES = [name for name, (type_, _) in CASES.items() if type_ != "strategy"]

# Template syntax that must never reach a generated file. `${{` is a GitHub Actions
# expression, not Jinja, so it is the one legitimate double brace.
RESIDUE = re.compile(r"(?<!\$)\{\{|\{%|\{#|\bjinja\b", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")


def _render(name: str, root: Path) -> Path:
    type_, extra = CASES[name]
    return templates.render(type_, root / name, {**COMMON, **extra})


class _Renders(dict):
    """Renders a case the first time a test asks for it, so a worker renders only its own."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    def __missing__(self, name: str) -> Path:
        self[name] = _render(name, self.root)
        return self[name]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> _Renders:
    return _Renders(tmp_path_factory.mktemp("rendered"))


def _files(project: Path) -> list[Path]:
    return sorted(p for p in project.rglob("*") if p.is_file())


def _tree(project: Path) -> dict[str, str]:
    return {
        p.relative_to(project).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in _files(project)
    }


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "APP_"))}
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _importable(*modules: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in modules)


def _has_jsonschema_stubs() -> bool:
    return any((Path(entry) / "jsonschema-stubs").is_dir() for entry in sys.path if entry)


# ------------------------------------------------------------------ the tree


def test_every_rendered_tree_matches_the_snapshot(rendered):
    trees = {name: _tree(rendered[name]) for name in CASES}
    if UPDATE:
        SNAPSHOT.write_text(json.dumps(trees, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name, tree in trees.items():
        assert tree == expected[name], (
            f"{name}: rendered output changed; if intended, rerun with "
            "UPDATE_TEMPLATE_SNAPSHOTS=1 and commit the snapshot"
        )


@pytest.mark.parametrize("name", CASES)
def test_no_template_syntax_survives(rendered, name):
    leftovers = []
    for path in _files(rendered[name]):
        relative = path.relative_to(rendered[name]).as_posix()
        if "{" in relative or relative.endswith(".jinja"):
            leftovers.append(relative)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RESIDUE.search(line):
                leftovers.append(f"{relative}:{number}: {line.strip()}")
    assert leftovers == []


@pytest.mark.parametrize("name", CASES)
def test_nothing_identifies_the_tool_or_a_person(rendered, name):
    credit = CASES[name][1].get("credit", False)
    project = rendered[name]
    assert not (project / ".copier-answers.yml").exists()
    for path in _files(project):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(project).as_posix()
        assert not EMAIL.search(text), f"{relative} contains an e-mail address"
        assert "copier" not in text.lower(), relative
        if not credit:
            assert "testkeel" not in text.lower(), relative


def test_credit_adds_one_footer_line_only_when_asked(rendered):
    readme = (rendered["api-rest-both-no-ci-py311"] / "README.md").read_text(encoding="utf-8")
    assert len([line for line in readme.splitlines() if "testkeel" in line.lower()]) == 1
    assert "testkeel" not in (rendered["api-rest"] / "README.md").read_text().lower()


def test_updatable_writes_the_answers_file(tmp_path):
    project = templates.render("api-rest", tmp_path / "p", {**COMMON, "updatable": True})
    answers = (project / ".copier-answers.yml").read_text(encoding="utf-8")
    assert "project_name: ACME Shop" in answers


def test_render_refuses_an_unknown_type_or_answer(tmp_path):
    with pytest.raises(ValueError, match="unknown assignment type"):
        templates.render("mobile", tmp_path, COMMON)
    with pytest.raises(ValueError, match="unknown template answers: colour"):
        templates.render("web-ui", tmp_path, {**COMMON, "colour": "blue"})


@pytest.mark.parametrize("name", CASES)
def test_the_task_brief_and_secrets_are_git_ignored(rendered, name):
    ignored = (rendered[name] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {".task/", ".env", "reports/"} <= set(ignored)


def test_strategy_ships_documents_only(rendered):
    for name in ("strategy", "strategy-no-ci"):
        suffixes = {p.suffix for p in _files(rendered[name])}
        assert ".py" not in suffixes and ".toml" not in suffixes, name
    plan = (rendered["strategy"] / "TEST_PLAN.md").read_text(encoding="utf-8")
    for heading in ("Context of testing", "Risk register", "Test strategy", "Schedule"):
        assert re.search(rf"^## \d+\. {heading}$", plan, re.MULTILINE), heading


# ------------------------------------------------------------------ the rules baked in


@pytest.mark.parametrize("name", [n for n in CODE_CASES if CASES[n][0] == "web-ui"])
def test_playwright_image_matches_the_pinned_client(rendered, name):
    project = rendered[name]
    pin = re.search(r'"playwright==([\d.]+)"', (project / "pyproject.toml").read_text())
    assert pin, "playwright must be pinned exactly"
    images = [
        f.read_text()
        for f in (project / "Dockerfile", project / ".github/workflows/ci.yml")
        if f.exists()
    ]
    assert images
    for text in images:
        tags = re.findall(r"playwright/python:v([\d.]+)-", text)
        assert tags and set(tags) == {pin.group(1)}


@pytest.mark.parametrize("name", CODE_CASES)
def test_tests_never_sleep_or_rerun_everything(rendered, name):
    project = rendered[name]
    for path in [*project.joinpath("tests").rglob("*.py"), *project.joinpath("src").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if "client" not in path.parts:  # the HTTP client's backoff is the one allowed wait
            assert "time.sleep" not in text and "wait_for_timeout" not in text, path
    assert "--reruns" not in (project / "pyproject.toml").read_text()


# ------------------------------------------------------------------ the code itself


@pytest.mark.parametrize("name", CODE_CASES)
def test_rendered_code_passes_its_own_lint_and_format_rules(rendered, name):
    project = rendered[name]
    for args in (["check", "."], ["format", "--check", "."]):
        result = _run([sys.executable, "-m", "ruff", *args], project)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", CODE_CASES)
def test_rendered_code_compiles(rendered, name):
    result = _run([sys.executable, "-m", "compileall", "-q", "src", "tests"], rendered[name])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not _importable("mypy", "httpx", "pydantic_settings", "playwright", "jsonschema"),
    reason="mypy or a generated project's dependency is not installed",
)
@pytest.mark.parametrize("name", CODE_CASES)
def test_rendered_code_type_checks(rendered, name):
    if CASES[name][0] == "api-rest" and not _has_jsonschema_stubs():
        pytest.skip("types-jsonschema is not installed")
    result = _run([sys.executable, "-m", "mypy"], rendered[name])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not _importable("httpx", "pydantic_settings", "jsonschema"),
    reason="the api-rest project's dependencies are not installed",
)
@pytest.mark.parametrize("name", [n for n in CODE_CASES if CASES[n][0] == "api-rest"])
def test_api_rest_offline_tests_pass_without_network(rendered, name):
    # The report plugins of the generated project are not dependencies of this repository,
    # so the run keeps the project's markers and selection but drops its report options.
    result = _run(
        [sys.executable, "-m", "pytest", "-m", "offline", "-p", "no:cacheprovider",
         "-o", "addopts=-ra --strict-markers"],
        rendered[name],
    )  # fmt: skip
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert re.search(r"\b\d{2,} passed\b", result.stdout), result.stdout[-1000:]


# ------------------------------------------------------------------ the examples

EXAMPLES = sorted(
    Path(__file__).resolve().parents[2].joinpath("examples").glob("*/.scaffold-manifest")
)


def _manifest(path: Path) -> tuple[str, dict[str, str], list[str]]:
    """``(type, answers, files)`` from a ``.scaffold-manifest``."""
    answers: dict[str, str] = {}
    files: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"# ([a-z_]+):(?: (.*))?", line)
        if match:
            answers[match.group(1)] = match.group(2) or ""
        elif line and not line.startswith("#"):
            files.append(line)
    return answers.pop("type"), answers, files


@pytest.mark.parametrize("manifest", EXAMPLES, ids=lambda path: path.parent.name)
def test_example_skeleton_is_what_the_template_renders_today(manifest, tmp_path):
    type_, answers, files = _manifest(manifest)
    assert files, "an empty manifest checks nothing"
    fresh = templates.render(type_, tmp_path / "fresh", answers)
    drifted = [
        name
        for name in files
        if (fresh / name).read_bytes() != (manifest.parent / name).read_bytes()
    ]
    assert drifted == [], (
        f"{manifest.parent.name}: these files no longer match the template; re-render the "
        "example or move the file out of .scaffold-manifest if it is now hand-written"
    )
