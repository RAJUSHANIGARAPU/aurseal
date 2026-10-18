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
    "api-graphql": ("api-graphql", {}),
    "api-graphql-allure-no-docker-py313": (
        "api-graphql",
        {"report": "allure", "docker": False, "python_version": "3.13"},
    ),
    "a11y": ("a11y", {}),
    "a11y-both-no-ci": ("a11y", {"report": "both", "ci": "none"}),
    "strategy": ("strategy", {}),
    "strategy-no-ci": ("strategy", {"ci": "none"}),
    "perf-k6": ("perf-k6", {}),
    "perf-k6-allure-no-docker-no-ci": (
        "perf-k6",
        {"report": "allure", "docker": False, "ci": "none"},
    ),
    "perf-locust": ("perf-locust", {}),
    "perf-locust-both-no-docker-py313": (
        "perf-locust",
        {"report": "both", "docker": False, "python_version": "3.13"},
    ),
}
CODE_CASES = [name for name, (type_, _) in CASES.items() if type_ != "strategy"]
BROWSER_TYPES = {"web-ui", "a11y"}

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


def _has_chromium() -> bool:
    if not _importable("playwright"):
        return False
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    except Exception:
        return False


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


@pytest.mark.parametrize("name", [n for n in CODE_CASES if CASES[n][0] in BROWSER_TYPES])
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
    if CASES[name][0] == "api-graphql" and not _importable("gql", "graphql"):
        pytest.skip("gql is not installed")
    if CASES[name][0] == "perf-locust" and not _importable("locust"):
        pytest.skip("locust is not installed")
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


@pytest.mark.skipif(
    not _importable("gql", "graphql", "httpx", "pydantic_settings"),
    reason="the api-graphql project's dependencies are not installed",
)
@pytest.mark.parametrize("name", [n for n in CODE_CASES if CASES[n][0] == "api-graphql"])
def test_api_graphql_offline_tests_pass_without_network(rendered, name):
    result = _run(
        [sys.executable, "-m", "pytest", "-m", "offline", "-p", "no:cacheprovider",
         "-o", "addopts=-ra --strict-markers"],
        rendered[name],
    )  # fmt: skip
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert re.search(r"\b\d{2,} passed\b", result.stdout), result.stdout[-1000:]


def test_api_graphql_operations_and_snapshot_ship_together(rendered):
    project = rendered["api-graphql"]
    assert (project / "schema.graphql").is_file()
    queries = list((project / "src" / "acme_shop" / "operations").glob("*.graphql"))
    assert queries, "the template must ship at least one operation file"


@pytest.mark.skipif(
    not _importable("axe_playwright_python", "pytest_playwright", "pydantic_settings")
    or not _has_chromium(),
    reason="the a11y project's dependencies or Chromium are not installed",
)
@pytest.mark.parametrize("name", [n for n in CODE_CASES if CASES[n][0] == "a11y"])
def test_a11y_offline_tests_pass_without_network(rendered, name):
    # The planted-violation fixtures are the positive control: they must be found offline.
    result = _run(
        [sys.executable, "-m", "pytest", "-m", "offline", "-p", "no:cacheprovider",
         "-o", "addopts=-ra --strict-markers"],
        rendered[name],
    )  # fmt: skip
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert re.search(r"\b\d{2,} passed\b", result.stdout), result.stdout[-1000:]


def test_a11y_ships_an_empty_baseline_and_both_fixtures(rendered):
    project = rendered["a11y"]
    baseline = json.loads((project / "a11y-baseline.json").read_text(encoding="utf-8"))
    assert baseline["violations"] == []
    for name in ("before.html", "after.html"):
        assert (project / "tests" / "fixtures" / "static" / name).is_file(), name


@pytest.mark.skipif(
    not _importable("pydantic_settings"), reason="the perf projects' dependencies are not installed"
)
@pytest.mark.parametrize("name", [n for n in CODE_CASES if CASES[n][0].startswith("perf-")])
def test_perf_offline_tests_pass_without_network(rendered, name):
    if CASES[name][0] == "perf-locust" and not _importable("locust"):
        pytest.skip("locust is not installed")
    result = _run(
        [sys.executable, "-m", "pytest", "-m", "offline", "-p", "no:cacheprovider",
         "-o", "addopts=-ra --strict-markers"],
        rendered[name],
    )  # fmt: skip
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert re.search(r"\b\d{2,} passed\b", result.stdout), result.stdout[-1000:]


@pytest.mark.parametrize("name", [n for n in CASES if CASES[n][0] == "perf-k6"])
def test_k6_image_matches_the_version_ci_installs(rendered, name):
    project = rendered[name]
    sources = [
        (path, pattern)
        for path, pattern in (
            (project / "Dockerfile", r"grafana/k6:([\d.]+)"),
            (project / ".github/workflows/ci.yml", r'k6-version: "([\d.]+)"'),
        )
        if path.exists()
    ]
    pins = {pin for path, pattern in sources for pin in re.findall(pattern, path.read_text())}
    assert len(pins) == (1 if sources else 0), pins


@pytest.mark.parametrize("name", [n for n in CASES if CASES[n][0].startswith("perf-")])
def test_load_profiles_are_guarded_and_ci_runs_only_the_smoke(rendered, name):
    project = rendered[name]
    makefile = (project / "Makefile").read_text(encoding="utf-8")
    for profile in ("load", "stress"):
        recipe = makefile.split(f"\n{profile}:", 1)[1].split("\n\n", 1)[0]
        assert f"$(GUARD) {profile} " in recipe, f"make {profile} must run the target guard first"
    workflow = project / ".github/workflows/ci.yml"
    if workflow.exists():
        text = workflow.read_text(encoding="utf-8")
        profiles = set(re.findall(r"(?:k6/scenarios|profiles)/(\w+)\.(?:js|conf)", text))
        assert profiles == {"smoke"}


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
