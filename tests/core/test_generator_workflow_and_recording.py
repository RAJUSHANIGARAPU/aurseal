"""``TestGenerator.generate_from_workflow`` / ``from_recording`` and the section splitter."""

from __future__ import annotations

import json

import pytest

from core.ai.test_generator import GeneratedFiles, split_sections
from core.ai.test_generator import TestGenerator as Generator
from tests.core._fake_claude import FakeClaude
from traceability.review import is_draft_source, unmark_draft

PAGE = "class OrdersPage:\n    pass\n"
TEST = "def test_x():\n    assert True\n"
BOTH = (
    f"===PAGE_OBJECT_START===\n{PAGE}===PAGE_OBJECT_END===\n"
    f"===TEST_START===\n{TEST}===TEST_END===\n"
)
LOCATORS = {"save": {"type": "testid", "value": "save-order"}}


@pytest.fixture
def gen(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return Generator()


def test_split_sections_reads_named_blocks_and_strips_fences():
    text = "noise\n===A_START===\n```python\nx = 1\n```\n===A_END===\n===B_START===\ny\n===B_END==="
    assert split_sections(text) == {"A": "x = 1\n", "B": "y\n"}
    assert split_sections(None) == {} and split_sections("===A_START===\nopen") == {}


def test_generate_from_workflow_returns_both_files(gen, tmp_path, monkeypatch):
    spec = tmp_path / "orders.md"
    spec.write_text("## Scenario place an order\n")
    fake = FakeClaude(BOTH).install(monkeypatch)
    out = gen.generate_from_workflow(spec, "orders")
    assert out == GeneratedFiles(PAGE, out.test)
    assert is_draft_source(out.test) and unmark_draft(out.test) == TEST
    assert (
        "class OrdersPage(BasePage)" in fake.prompt and "## Scenario place an order" in fake.prompt
    )


@pytest.mark.parametrize("spec_text", [None, "  \n"], ids=["missing", "empty"])
def test_generate_from_workflow_without_a_spec_makes_no_call(gen, tmp_path, monkeypatch, spec_text):
    spec = tmp_path / "orders.md"
    if spec_text is not None:
        spec.write_text(spec_text)
    fake = FakeClaude(BOTH).install(monkeypatch)
    assert gen.generate_from_workflow(spec, "orders") is None
    assert fake.prompts == []


def test_from_recording_returns_validated_locators(gen, tmp_path, monkeypatch):
    rec = tmp_path / "orders.spec.ts"
    rec.write_text("await page.getByTestId('save-order').click();\n")
    completion = BOTH + f"===LOCATORS_START===\n{json.dumps(LOCATORS)}\n===LOCATORS_END===\n"
    fake = FakeClaude(completion).install(monkeypatch)
    out = gen.from_recording(rec, "orders")
    assert out == GeneratedFiles(PAGE, out.test, LOCATORS)
    assert is_draft_source(out.test) and unmark_draft(out.test) == TEST
    assert "getByTestId('save-order')" in fake.prompt
    assert "RECORDING (.ts)" in fake.prompt


def test_from_recording_without_api_key_makes_no_call(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rec = tmp_path / "r.py"
    rec.write_text("page.click('text=Save')\n")
    fake = FakeClaude(BOTH).install(monkeypatch)
    assert Generator().from_recording(rec, "r") is None
    assert fake.prompts == []


def test_from_recording_rejects_a_completion_without_locators(gen, tmp_path, monkeypatch):
    rec = tmp_path / "r.py"
    rec.write_text("page.click('text=Save')\n")
    FakeClaude(BOTH).install(monkeypatch)
    assert gen.from_recording(rec, "r") is None
