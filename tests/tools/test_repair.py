"""tools.repair: a proposal is kept only when the checks pass and no guardrail is crossed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import repair
from tools.repair import Check

LINT = [Check("ruff check", ("ruff", "check", "."))]


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _reply(files: dict[str, str], summary: str = "fixed it") -> str:
    body = {"summary": summary, "files": [{"path": p, "content": c} for p, c in files.items()]}
    return "```json\n" + json.dumps(body) + "\n```"


def _runner(root: Path, rel: str, good: str):
    """Fails, naming ``rel``, until that file holds ``good``."""

    def run(argv):
        if (root / rel).read_text() == good:
            return 0, "All checks passed!"
        return 1, f"{rel}:1:1: F401 `os` imported but unused"

    return run


def test_a_verified_proposal_is_kept(tmp_path):
    root = _tree(tmp_path, {"pkg/mod.py": "import os\nx = 1\n"})
    result = repair.repair(
        LINT,
        lambda messages: _reply({"pkg/mod.py": "x = 1\n"}),
        runner=_runner(root, "pkg/mod.py", "x = 1\n"),
        root=root,
    )
    assert result.status == "fixed"
    assert result.changed == ["pkg/mod.py"]
    assert (root / "pkg/mod.py").read_text() == "x = 1\n"


def test_a_proposal_that_does_not_pass_is_rolled_back(tmp_path):
    root = _tree(tmp_path, {"pkg/mod.py": "import os\nx = 1\n"})
    result = repair.repair(
        LINT,
        lambda messages: _reply({"pkg/mod.py": "import os\nx = 2\n"}),
        runner=_runner(root, "pkg/mod.py", "x = 1\n"),
        root=root,
    )
    assert result.status == "unfixable"
    assert (root / "pkg/mod.py").read_text() == "import os\nx = 1\n"


def test_the_second_attempt_sees_why_the_first_was_refused(tmp_path):
    root = _tree(tmp_path, {"pkg/mod.py": "import os\nx = 1\n"})
    prompts = []
    replies = iter(
        [_reply({"pkg/mod.py": "import os  # noqa\nx = 1\n"}), _reply({"pkg/mod.py": "x = 1\n"})]
    )

    def complete(messages):
        prompts.append(messages[-1]["content"])
        return next(replies)

    result = repair.repair(LINT, complete, runner=_runner(root, "pkg/mod.py", "x = 1\n"), root=root)
    assert result.status == "fixed"
    assert "noqa" in prompts[1] and "Refused" in prompts[1]


@pytest.mark.parametrize(
    ("files", "proposal", "reason"),
    [
        (
            {"pkg/mod.py": "import os\n", "pkg/other.py": "y = 1\n"},
            {"pkg/other.py": "y = 2\n"},
            "not named by the failure",
        ),
        ({"pkg/mod.py": "import os\n"}, {"pkg/new.py": "z = 1\n"}, "creates a file"),
        ({"pkg/mod.py": "import os\n"}, {"pkg/mod.py": "import os  # noqa: F401\n"}, "escape"),
        (
            {"pkg/mod.py": "import os\n"},
            {"pkg/mod.py": "import pytest\npytestmark = pytest.mark.skip\n"},
            "escape",
        ),
    ],
)
def test_proposals_that_dodge_the_check_are_refused(tmp_path, files, proposal, reason):
    root = _tree(tmp_path, files)
    before = {rel: (root / rel).read_text() for rel in files}
    result = repair.repair(
        LINT,
        lambda messages: _reply(proposal),
        runner=lambda argv: (1, "pkg/mod.py:1:1: F401 `os` imported but unused"),
        root=root,
    )
    assert result.status == "unfixable"
    assert any(reason in note for note in result.notes), result.notes
    assert {rel: (root / rel).read_text() for rel in files} == before
    assert not (root / "pkg/new.py").exists()


def test_removing_an_assertion_from_a_test_is_refused():
    old = "def test_x():\n    assert f() == 1\n    assert g() == 2\n"
    new = "def test_x():\n    assert f() == 1\n"
    assert repair.violations("tests/test_x.py", old, new, ["tests/test_x.py"]) == [
        "tests/test_x.py: removes an assertion"
    ]


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/tests.yml",
        "checks/mutation/mutation.toml",
        "requirements.txt",
        "tests/gate/conftest.py",
        "visual_baselines/home.png",
    ],
)
def test_guarded_paths_are_never_offered_or_accepted(tmp_path, path):
    root = _tree(tmp_path, {path: "x\n"})
    assert repair.is_guarded(path)
    assert repair.named_files([f"{path}:1:1: E999"], root) == []
    assert any("guarded" in v for v in repair.violations(path, "x\n", "y\n", [path]))


def test_named_files_resolve_absolute_paths_inside_the_checkout(tmp_path):
    root = _tree(tmp_path, {"gate/shard.py": "", "tests/gate/test_shard.py": ""})
    output = (
        f"{root}/gate/shard.py:12: in plan\n"
        "tests/gate/test_shard.py:40: AssertionError\n"
        "/usr/lib/python3.13/json/__init__.py:1: in loads\n"
    )
    assert repair.named_files([output], root) == ["gate/shard.py", "tests/gate/test_shard.py"]


def test_failed_tests_are_read_from_the_log_and_must_exist(tmp_path):
    root = _tree(tmp_path, {"tests/test_a.py": ""})
    log = (
        "Offline suite\tRun pytest\t2026-10-25T07:40:01Z FAILED tests/test_a.py::test_one - "
        "assert 1 == 2\n"
        "FAILED tests/test_a.py::test_two[chromium]\n"
        "FAILED tests/gone.py::test_three\n"
    )
    assert repair.failed_tests(log, root) == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two[chromium]",
    ]
    checks = repair.checks_for(["tests"], log, root)
    assert [c.name for c in checks] == ["failed tests"]
    assert checks[0].argv[-2:] == (
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two[chromium]",
    )


def test_outcomes_without_a_proposal(tmp_path):
    passing = lambda argv: (0, "")  # noqa: E731
    failing = lambda argv: (1, "pkg/mod.py:1:1: F401")  # noqa: E731
    assert repair.repair([], None, runner=failing, root=tmp_path).status == "out_of_scope"
    assert repair.repair(LINT, None, runner=passing, root=tmp_path).status == "clean"
    assert repair.repair(LINT, None, runner=failing, root=tmp_path).status == "no_model"


def test_an_unreadable_reply_is_retried_then_given_up(tmp_path):
    root = _tree(tmp_path, {"pkg/mod.py": "import os\n"})
    result = repair.repair(
        LINT,
        lambda messages: "I cannot help with that.",
        runner=lambda argv: (1, "pkg/mod.py:1:1: F401"),
        root=root,
    )
    assert result.status == "unfixable"
    assert len(result.notes) == 2 and all("unreadable" in n for n in result.notes)


def test_main_writes_the_status_output_and_summary(tmp_path, monkeypatch):
    log, summary, output = tmp_path / "failed.log", tmp_path / "repair.md", tmp_path / "out"
    log.write_text("")
    monkeypatch.setenv("REPAIR_SCOPE", "tests")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("RUN_ID", "123")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert repair.main(["--log", str(log), "--summary", str(summary)]) == 0
    assert output.read_text() == "status=out_of_scope\n"
    assert "main run 123: **out_of_scope**" in summary.read_text()
