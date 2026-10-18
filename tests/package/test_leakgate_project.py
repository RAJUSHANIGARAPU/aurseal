"""Leak gate, generated-project rules: hosts, tool traces, the lock file index, standalone.

These rules run for ``new-assignment`` and ``assignment check`` only; the repository scan
must stay unaffected, which the rule-set tests at the end pin. Hosts are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testkeel.cli import app
from testkeel.leakgate import CLEAN, FINDINGS, ProjectRules, run
from testkeel.leakgate.__main__ import main
from testkeel.leakgate.project import ALLOW_HOSTS_PATH, PUBLIC_SOURCE, RULES

PRIVATE = "intranet.quuxcorp.io"
runner = CliRunner()


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)
    for name, content in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(content, encoding="utf-8")
    return tree


def _gate(tmp_path, home, files, rules: ProjectRules | None = None):
    tree = _tree(tmp_path, files)
    environ = {"HOME": str(home), "LEAK_DENYLIST": ""}
    rules = ProjectRules.for_project(["https://shop.acme.example.org"]) if rules is None else rules
    return run(trees=[tree], environ=environ, home=home, missing_list="warn", project=rules)


def _found(outcome) -> list[str]:
    return [str(f) for f in outcome.findings]


# ------------------------------------------------------------------ L4 hosts


@pytest.mark.parametrize(
    "line",
    [
        'BASE_URL = "https://www.saucedemo.com/inventory.html"',
        "see https://docs.astral.sh/uv/ and https://playwright.dev/python/",
        "http://localhost:8000 http://127.0.0.1:3001 http://10.0.0.5/ http://sut:3001/",
        "https://api.example.test https://example.com/x https://host.docker.internal:80",
        "FROM mcr.microsoft.com/playwright/python:v1.58.0-noble",
        "    container: ghcr.io/grafana/quickpizza-local:latest",
        "the template placeholder https://... stays as text",
    ],
)
def test_declared_demo_tooling_and_private_hosts_are_clean(line):
    rules = ProjectRules.for_project(["https://shop.acme.dev"])
    assert rules.content("README.md", line + "\n") == []


@pytest.mark.parametrize(
    "line",
    [
        f"see https://{PRIVATE}/wiki/page",
        f"git clone ssh://git@{PRIVATE}/team/repo.git",
        f"FROM {PRIVATE}:5000/base/python:3.12",
        "http://8.8.8.8/",
    ],
)
def test_an_unlisted_host_hits(line):
    rules = ProjectRules.for_project(["https://shop.acme.dev"])
    assert rules.content("README.md", "# title\n" + line + "\n") == [(2, "host.unlisted")]


def test_the_declared_target_and_its_subdomains_are_allowed():
    rules = ProjectRules.for_project(["https://shop.acme.dev/start"])
    assert rules.content("a.md", "https://api.shop.acme.dev/v1\n") == []
    assert rules.content("a.md", "https://acme.dev/\n") == [(1, "host.unlisted")]


def test_extra_hosts_come_from_the_flag_and_the_config_file(home):
    assert ProjectRules.for_project().content("a.md", f"https://{PRIVATE}/\n")
    flagged = ProjectRules.for_project(extra_hosts=[PRIVATE.upper()])
    assert flagged.content("a.md", f"https://{PRIVATE}/\n") == []
    (home / ALLOW_HOSTS_PATH).parent.mkdir(parents=True)
    (home / ALLOW_HOSTS_PATH).write_text(f"# extra public hosts\n{PRIVATE}  # docs\n", "utf-8")
    configured = ProjectRules.for_project(home=home)
    assert configured.content("a.md", f"https://{PRIVATE}/\n") == []


def test_a_host_finding_never_prints_the_host(tmp_path, home):
    outcome = _gate(tmp_path, home, {"docs/notes.md": f"x\nhttps://{PRIVATE}/a\n"})
    assert outcome.code == FINDINGS
    assert _found(outcome) == ["docs/notes.md:2 host.unlisted"]
    assert "quuxcorp" not in repr(outcome)


# ------------------------------------------------------------------ L7 tool traces


@pytest.mark.parametrize(
    ("path", "where"),
    [
        (".claude/settings.json", ".claude/"),
        ("src/.cursorrules", "src/.cursorrules"),
        (".cursor/rules/a.mdc", ".cursor/"),
        ("CLAUDE.md", "CLAUDE.md"),
        ("docs/AGENTS.md", "docs/AGENTS.md"),
        (".copier-answers.yml", ".copier-answers.yml"),
    ],
)
def test_tool_traces_hit(tmp_path, home, path, where):
    outcome = _gate(tmp_path, home, {path: "x\n", "README.md": "# ok\n"})
    assert _found(outcome) == [f"{where} trace.tool"]


def test_the_answers_file_is_allowed_for_an_updatable_project(tmp_path, home):
    files = {".copier-answers.yml": "_src_path: /somewhere/else\n"}
    assert _gate(tmp_path, home, files, ProjectRules(updatable=True)).code == CLEAN
    assert _gate(tmp_path, home, files, ProjectRules()).code == FINDINGS
    public = {".copier-answers.yml": f"_commit: testkeel-v0.1.0\n_src_path: {PUBLIC_SOURCE}\n"}
    assert _gate(tmp_path, home, public, ProjectRules()).code == CLEAN


# ------------------------------------------------------------------ L8 lock file index


LOCK = """version = 1

[[package]]
name = "pytest"
version = "8.3.0"
source = {{ registry = "{index}" }}
sdist = {{ url = "https://files.pythonhosted.org/packages/pytest-8.3.0.tar.gz" }}
"""


def test_a_lock_file_from_pypi_is_clean():
    rules = ProjectRules()
    assert rules.content("uv.lock", LOCK.format(index="https://pypi.org/simple")) == []


def test_a_lock_file_naming_another_index_hits():
    lock = LOCK.format(index=f"https://{PRIVATE}/simple")
    assert ProjectRules().content("uv.lock", lock) == [(6, "lock.index")]
    # the declared target does not widen what a lock file may name
    rules = ProjectRules.for_project([f"https://{PRIVATE}"])
    assert rules.content("uv.lock", lock) == [(6, "lock.index")]


# ------------------------------------------------------------------ L9 standalone


def test_importing_the_tool_hits_with_the_line():
    code = "import os\n\nfrom testkeel.model import v1\nimport testkeel_demo\nimport testkeel\n"
    assert ProjectRules().content("tests/test_a.py", code) == [
        (3, "standalone.import"),
        (5, "standalone.import"),
    ]


def test_a_file_that_does_not_parse_is_left_to_the_self_check():
    assert ProjectRules().content("tests/test_a.py", "def broken(:\n") == []


@pytest.mark.parametrize(
    ("name", "text", "line"),
    [
        ("pyproject.toml", '[project]\ndependencies = [\n    "TestKeel>=0.1",\n]\n', 3),
        ("pyproject.toml", '[dependency-groups]\ndev = ["testkeel[report]"]\n', 2),
        ("requirements.txt", "pytest\ntestkeel==0.1.0\n", 2),
        ("uv.lock", 'version = 1\n\n[[package]]\nname = "testkeel"\n', 4),
    ],
)
def test_depending_on_the_tool_hits(name, text, line):
    assert ProjectRules().content(name, text) == [(line, "standalone.dependency")]


def test_similar_names_and_comments_are_not_a_dependency():
    text = '[project]\nname = "testkeel-demo"\ndependencies = ["testkeel_x"]\n# testkeel\n'
    assert ProjectRules().content("pyproject.toml", text) == []


def test_the_task_brief_is_not_held_to_the_project_rules():
    assert ProjectRules().content(".task/TASK.md", f"Submit at https://{PRIVATE}/\n") == []


# ------------------------------------------------------------------ rule-set selection

TRACED = {
    ".claude/settings.json": "{}\n",
    "README.md": f"https://{PRIVATE}/\n",
    "src/a.py": "import testkeel\n",
}


def test_without_project_rules_the_gate_is_the_repository_gate(tmp_path, home):
    tree = _tree(tmp_path, TRACED)
    environ = {"HOME": str(home), "LEAK_DENYLIST": ""}
    outcome = run(trees=[tree], environ=environ, home=home, missing_list="warn")
    assert outcome.code == CLEAN
    with_rules = run(
        trees=[tree], environ=environ, home=home, missing_list="warn", project=ProjectRules()
    )
    assert {f.rule for f in with_rules.findings} == {
        "trace.tool",
        "host.unlisted",
        "standalone.import",
    }


def test_the_repository_entry_point_never_applies_project_rules(tmp_path, home, monkeypatch):
    tree = _tree(tmp_path, TRACED)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LEAK_DENYLIST", "")
    assert main(["scan", "--tree", str(tree), "--missing-list", "warn"]) == CLEAN


def test_every_rule_id_is_namespaced():
    assert all(rule.split(".")[0] in {"host", "trace", "lock", "standalone"} for rule in RULES)


# ------------------------------------------------------------------ the CLI


def test_assignment_check_applies_the_project_rules(tmp_path, home):
    tree = _tree(tmp_path, {"README.md": f"# Shop\nhttps://{PRIVATE}/\n"})
    env = {"HOME": str(home), "LEAK_DENYLIST": ""}
    found = runner.invoke(app, ["assignment", "check", str(tree), "--json"], env=env)
    assert found.exit_code == 4
    assert json.loads(found.output)["findings"] == [
        {"location": "README.md", "line": 2, "rule": "host.unlisted", "entry": None}
    ]
    assert "quuxcorp" not in found.output
    allowed = runner.invoke(
        app, ["assignment", "check", str(tree), "--allow-host", PRIVATE], env=env
    )
    assert allowed.exit_code == 0, allowed.output


def test_new_assignment_refuses_a_rendered_trace(tmp_path, home):
    from testkeel.assignment import scaffold

    def render(_type, destination, _answers):
        (destination / "README.md").write_text("# Shop\n", "utf-8")
        (destination / ".claude").mkdir()
        (destination / ".claude" / "settings.json").write_text("{}\n", "utf-8")

    result = scaffold.new_assignment(
        scaffold.Options(type="strategy", name="Shop", git=False, lock=False,
                         output_dir=tmp_path / "shop"),
        staging_root=tmp_path / "staging",
        environ={"HOME": str(home), "LEAK_DENYLIST": ""},
        renderer=render,
    )  # fmt: skip
    assert result.code == 4
    assert ".claude/ trace.tool" in result.lines
    assert not (tmp_path / "shop").exists()


# ------------------------------------------------------------------ the real templates


@pytest.mark.parametrize("type_", ["web-ui", "api-rest", "strategy"])
@pytest.mark.parametrize("extra", [{}, {"report": "both", "credit": True, "ci": "github"}])
def test_the_packaged_templates_pass_the_project_rules(tmp_path, home, type_, extra):
    pytest.importorskip("copier", reason="copier is not installed")
    from testkeel.templates import render

    answers = {
        "project_name": "ACME Shop",
        "project_slug": "acme-shop",
        "company": "ACME",
        "target_url": "https://shop.example.test",
        "api_base_url": "https://api.example.test",
        **extra,
    }
    project = render(type_, tmp_path / "out", answers)
    (project / ".copier-answers.yml").unlink(missing_ok=True)
    outcome = run(
        trees=[project],
        environ={"HOME": str(home), "LEAK_DENYLIST": ""},
        home=home,
        missing_list="warn",
        project=ProjectRules.for_project([answers["target_url"], answers["api_base_url"]]),
    )
    assert _found(outcome) == []
