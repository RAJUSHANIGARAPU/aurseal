"""
A generation that produced nothing must not overwrite the file it was aimed at.

The generator tools took whatever ``TestGenerator`` returned and wrote it to
``output_path``, then reported ``generated: True``. With ``reports/traces/``
empty — a fresh checkout, CI, or traces cleaned between runs — an existing
``ui/tests/test_search.py`` was truncated to the single line
``# No trace file found``, or to ``Claude API error: ...``, which is not even
valid Python. The caller was told it had succeeded.

So there are three things to hold, and the third is the one that stops the
other two being satisfied by a tool that does nothing at all:

1. a failed generation reports ``generated: False``;
2. a failed generation leaves the target file exactly as it found it;
3. a real generation still writes the file and still reports success.
"""

from __future__ import annotations

import pytest

from core.agents.generator_agent import GeneratorAgent
from core.agents.planner_agent import PlannerAgent
from tests.core._fake_claude import GENERATED, FakeClaude, write_trace

EXISTING = '''"""Hand-written test that must survive a failed generation."""


def test_search_returns_results(page):
    assert page is not None
'''

SPEC = """---
seed: false
feature: wiki_search
---

## Scenario searching for an article

### Steps
1. Type `Playwright testing framework` into the search box
"""


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return tmp_path


@pytest.fixture
def target(workdir):
    """An existing test file, with real content, at the tool's output_path."""
    path = workdir / "ui" / "tests" / "test_search.py"
    path.parent.mkdir(parents=True)
    path.write_text(EXISTING)
    return path


class TestGenerateFromTraceWithNoTrace:
    """reports/traces/ is empty — the scenario that truncated real tests."""

    def test_it_reports_failure(self, workdir, target):
        result = GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert result["generated"] is False

    def test_it_says_why(self, workdir, target):
        result = GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert "error" in result

    def test_the_existing_file_is_untouched(self, workdir, target):
        GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert target.read_text() == EXISTING

    def test_no_file_is_created_where_there_was_none(self, workdir):
        out = workdir / "ui" / "tests" / "test_new.py"
        GeneratorAgent()._tool_generate_from_trace(output_path=str(out))
        assert not out.exists()


class TestGenerateFromTraceWhenTheApiFails:
    def test_it_reports_failure(self, workdir, target, monkeypatch):
        write_trace(workdir)
        FakeClaude(error=RuntimeError("503 Service Unavailable")).install(monkeypatch)
        result = GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert result["generated"] is False

    def test_the_error_text_never_reaches_the_file(self, workdir, target, monkeypatch):
        write_trace(workdir)
        FakeClaude(error=RuntimeError("503 Service Unavailable")).install(monkeypatch)
        GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert target.read_text() == EXISTING


class TestGenerateFromTraceSucceeds:
    """
    Positive control. Everything above passes for a tool that writes nothing
    and always reports failure.
    """

    def test_it_reports_success(self, workdir, target, monkeypatch):
        write_trace(workdir)
        FakeClaude().install(monkeypatch)
        result = GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert result["generated"] is True

    def test_it_writes_the_generated_code(self, workdir, target, monkeypatch):
        write_trace(workdir)
        FakeClaude().install(monkeypatch)
        GeneratorAgent()._tool_generate_from_trace(output_path=str(target))
        assert target.read_text() == GENERATED

    def test_it_creates_the_output_directory(self, workdir, monkeypatch):
        write_trace(workdir)
        FakeClaude().install(monkeypatch)
        out = workdir / "ui" / "tests" / "test_new.py"
        GeneratorAgent()._tool_generate_from_trace(output_path=str(out))
        assert out.read_text() == GENERATED


class TestGenerateFromSpec:
    """
    The tool used to guard on ``hasattr(generator, "generate_from_spec")``,
    which was always False, so every call fell through to "generate from the
    latest trace, named after the spec file". The spec was never opened — a
    nonexistent path was not even an error.
    """

    def test_a_missing_spec_reports_failure(self, workdir, target):
        result = GeneratorAgent()._tool_generate_from_spec(
            spec_path=str(workdir / "nope.md"), output_path=str(target)
        )
        assert result["generated"] is False

    def test_a_missing_spec_leaves_the_target_alone(self, workdir, target):
        GeneratorAgent()._tool_generate_from_spec(
            spec_path=str(workdir / "nope.md"), output_path=str(target)
        )
        assert target.read_text() == EXISTING

    def test_a_missing_spec_does_not_fall_back_to_a_trace(self, workdir, target, monkeypatch):
        """A trace is present and must not be used in place of the spec."""
        write_trace(workdir)
        fake = FakeClaude().install(monkeypatch)
        result = GeneratorAgent()._tool_generate_from_spec(
            spec_path=str(workdir / "nope.md"), output_path=str(target)
        )
        assert result["generated"] is False
        assert fake.prompts == []
        assert target.read_text() == EXISTING


class TestGenerateFromSpecSucceeds:
    """Positive control, and proof the spec itself is what gets generated from."""

    @pytest.fixture
    def spec(self, workdir):
        path = workdir / "specs" / "wiki-search.md"
        path.parent.mkdir(parents=True)
        path.write_text(SPEC)
        return path

    def test_it_writes_the_generated_code(self, workdir, target, spec, monkeypatch):
        FakeClaude().install(monkeypatch)
        result = GeneratorAgent()._tool_generate_from_spec(
            spec_path=str(spec), output_path=str(target)
        )
        assert result["generated"] is True
        assert target.read_text() == GENERATED

    def test_the_spec_body_reaches_the_prompt(self, workdir, target, spec, monkeypatch):
        fake = FakeClaude().install(monkeypatch)
        GeneratorAgent()._tool_generate_from_spec(spec_path=str(spec), output_path=str(target))
        assert "Playwright testing framework" in fake.prompt


class TestPlannerGenerateTestWithNoTrace:
    """The planner's own copy of the same tool had the same defect."""

    def test_it_reports_failure(self, workdir, target):
        result = PlannerAgent()._tool_generate_test(output_path=str(target))
        assert result["generated"] is False

    def test_the_existing_file_is_untouched(self, workdir, target):
        PlannerAgent()._tool_generate_test(output_path=str(target))
        assert target.read_text() == EXISTING


class TestPlannerGenerateTestSucceeds:
    """Positive control for the two above."""

    def test_it_writes_the_generated_code(self, workdir, target, monkeypatch):
        write_trace(workdir)
        FakeClaude().install(monkeypatch)
        result = PlannerAgent()._tool_generate_test(output_path=str(target))
        assert result["generated"] is True
        assert target.read_text() == GENERATED
