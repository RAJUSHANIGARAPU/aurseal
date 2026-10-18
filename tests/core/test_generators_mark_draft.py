"""Every generator's output carries the draft marker, and the drafts are valid Python."""

from __future__ import annotations

import ast

from api.agents.generation import GenerationAgent
from api.agents.ingestion import PostmanRequest
from core.ai.test_generator import TestGenerator as Generator
from tests.core._fake_claude import FakeClaude, write_trace
from traceability.review import is_draft_source
from web_discovery.scenario_generator.models import ScenarioType, TestScenario
from web_discovery.script_generator.generator import ScriptGenerator


def _check(text: str, generator: str) -> None:
    assert is_draft_source(text), text
    assert f"generator={generator!r}" in text
    ast.parse(text)


def test_trace_and_spec_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    write_trace(tmp_path)
    FakeClaude().install(monkeypatch)
    _check(Generator().generate_from_trace(page_name="wiki"), "trace")
    spec = tmp_path / "s.md"
    spec.write_text("## Scenario one\n### Steps\n1. open\n")
    _check(Generator().generate_from_spec(spec), "spec")


def test_web_discovery_scripts(tmp_path):
    scenario = TestScenario(
        id="s1",
        name="page loads",
        scenario_type=list(ScenarioType)[0],
        page_url="https://site.example.test/a",
        page_title="A",
    )
    files = ScriptGenerator(output_dir=tmp_path).generate([scenario], "https://site.example.test")
    tests = [f for f in files if f.name.startswith("test_")]
    assert tests
    _check(tests[0].read_text(), "web-discovery")


def test_postman_generation(tmp_path):
    req = PostmanRequest(name="list orders", method="GET", url="https://api.example.test/orders")
    files = GenerationAgent(output_dir=tmp_path).generate([req])
    _check(files[0].read_text(), "postman")
