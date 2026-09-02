"""
A failed generation must not look like generated code.

``TestGenerator`` used to return its failures as ordinary strings —
``# No trace file found``, ``# ANTHROPIC_API_KEY not set``,
``Claude API error: ...`` — on the same channel as the source it produces. The
callers write whatever comes back straight to a ``.py`` file, so those messages
landed on disk as the generated test. ``AutoFixer.fix_file`` already returns a
falsy value for exactly these conditions; these tests hold the generator to the
same contract.

The second half of the page is the half that keeps this honest. "Return None"
is trivially satisfied by a function that never generates anything, so every
failure class below is paired with a control asserting that a real generation
still comes back as code — and, for the spec path, that the spec was read.
"""

from __future__ import annotations

import pytest

from core.ai.test_generator import TestGenerator as Generator
from tests.core._fake_claude import GENERATED, FakeClaude, write_trace

SPEC = """---
seed: false
feature: wiki_search
---

## Scenario searching for an article

### Steps
1. Type `Playwright testing framework` into the search box

### Expected
- The page heading is visible
"""


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run with cwd inside tmp_path — the trace lookup is cwd-relative."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def generator(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return Generator()


class TestTraceGenerationFailures:
    """Each of these used to come back as a string and get written to disk."""

    def test_no_trace_found_returns_none(self, workdir, generator):
        assert generator.generate_from_trace() is None

    def test_missing_api_key_returns_none(self, workdir, monkeypatch):
        write_trace(workdir)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert Generator().generate_from_trace() is None

    def test_api_error_returns_none(self, workdir, generator, monkeypatch):
        write_trace(workdir)
        FakeClaude(error=RuntimeError("503 Service Unavailable")).install(monkeypatch)
        assert generator.generate_from_trace() is None

    def test_an_empty_completion_returns_none(self, workdir, generator, monkeypatch):
        """Nothing useful came back; writing "" would still truncate a file."""
        write_trace(workdir)
        FakeClaude(text="").install(monkeypatch)
        assert generator.generate_from_trace() is None


class TestTraceGenerationSucceeds:
    """
    Positive control. Every assertion above is satisfied by a generator that
    returns None unconditionally.
    """

    def test_the_generated_code_is_returned(self, workdir, generator, monkeypatch):
        write_trace(workdir)
        FakeClaude().install(monkeypatch)
        assert generator.generate_from_trace(page_name="wiki_search") == GENERATED

    def test_the_page_name_reaches_the_prompt(self, workdir, generator, monkeypatch):
        write_trace(workdir)
        fake = FakeClaude().install(monkeypatch)
        generator.generate_from_trace(page_name="wiki_search")
        assert "wiki_search" in fake.prompt


class TestSpecGenerationFailures:
    def test_a_missing_spec_returns_none(self, workdir, generator):
        assert generator.generate_from_spec(workdir / "nope.md") is None

    def test_a_missing_spec_makes_no_api_call(self, workdir, generator, monkeypatch):
        fake = FakeClaude().install(monkeypatch)
        generator.generate_from_spec(workdir / "nope.md")
        assert fake.prompts == []

    def test_an_empty_spec_returns_none(self, workdir, generator):
        spec = workdir / "blank.md"
        spec.write_text("   \n")
        assert generator.generate_from_spec(spec) is None

    def test_an_api_error_returns_none(self, workdir, generator, monkeypatch):
        spec = workdir / "wiki-search.md"
        spec.write_text(SPEC)
        FakeClaude(error=RuntimeError("boom")).install(monkeypatch)
        assert generator.generate_from_spec(spec) is None


class TestSpecGenerationReadsTheSpec:
    """
    Positive control, and the point of the method. Generating from a spec has
    to be generation *from that spec* — not from an unrelated latest trace with
    the spec's filename bolted on, which is what the agent used to fall back to.
    """

    @pytest.fixture
    def spec(self, workdir):
        path = workdir / "wiki-search.md"
        path.write_text(SPEC)
        return path

    def test_the_generated_code_is_returned(self, generator, spec, monkeypatch):
        FakeClaude().install(monkeypatch)
        assert generator.generate_from_spec(spec) == GENERATED

    def test_the_spec_body_reaches_the_prompt(self, generator, spec, monkeypatch):
        fake = FakeClaude().install(monkeypatch)
        generator.generate_from_spec(spec)
        assert "Playwright testing framework" in fake.prompt
        assert "searching for an article" in fake.prompt

    def test_the_page_name_defaults_to_the_spec_stem(self, generator, spec, monkeypatch):
        fake = FakeClaude().install(monkeypatch)
        generator.generate_from_spec(spec)
        assert "wiki_search" in fake.prompt

    def test_an_explicit_page_name_wins(self, generator, spec, monkeypatch):
        fake = FakeClaude().install(monkeypatch)
        generator.generate_from_spec(spec, page_name="article_lookup")
        assert "article_lookup" in fake.prompt

    def test_no_trace_is_needed(self, generator, spec, monkeypatch):
        """There is no reports/traces/ under tmp_path, and that is fine."""
        FakeClaude().install(monkeypatch)
        assert generator.generate_from_spec(spec) is not None


class TestPageObjectGeneration:
    def test_missing_api_key_returns_none(self, workdir, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert Generator().generate_page_object("Search", "/w/index.php", "search page") is None

    def test_a_real_generation_still_returns_code(self, workdir, generator, monkeypatch):
        """Positive control for the assertion above."""
        FakeClaude().install(monkeypatch)
        out = generator.generate_page_object("Search", "/w/index.php", "search page")
        assert out == GENERATED
