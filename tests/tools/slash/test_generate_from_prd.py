"""generate-from-prd: ingestion, the vagueness gate, the four groups, the spec file."""

from __future__ import annotations

import json

import pytest

from tests.tools.slash._commands import claude_md_lists, read_command
from tools.slash import prd
from traceability.gaps import SpecSource

SENTENCE = "A signed-in shopper can apply one voucher code at checkout and should see the discount."


def plan(*groups, na=None):
    scenarios = [
        {
            "name": f"{g} case",
            "group": g,
            "preconditions": ["signed in"],
            "steps": ["open checkout", "enter a code"],
            "expected": ["the total shows the discount"],
            "tags": ["ui", "regression"],
        }
        for g in groups
    ]
    return prd.Plan(scenarios, na or {})


def test_inline_one_sentence_is_testable_and_needs_every_group():
    assert prd.ingest(SENTENCE) == SENTENCE
    assert prd.vague(SENTENCE) == []
    assert prd.check(plan(*prd.GROUPS)) == []
    missing = prd.check(plan("main", "validation"))
    assert any("boundary" in p for p in missing) and any("prevented" in p for p in missing)
    assert (
        prd.check(plan("main", "validation", "prevented", na={"boundary": "no limits stated"}))
        == []
    )


def test_the_spec_is_written_in_the_existing_format(tmp_path):
    path = prd.write("voucher_codes", plan(*prd.GROUPS), tmp_path)
    assert path.name == "voucher-codes.md"
    titles = [item.title for item in SpecSource(tmp_path).items()]
    assert titles == [f"{g} case" for g in prd.GROUPS]
    text = path.read_text(encoding="utf-8")
    for heading in ("### Preconditions", "### Steps", "### Expected", "### Tags"):
        assert heading in text
    assert text.startswith("---\nseed: true\nfeature: voucher_codes\n---")


def test_a_url_is_reduced_to_text_and_capped():
    html = "<html><head><style>x{}</style></head><body><h1>Vouchers</h1><script>evil()</script>"
    html += "<p>" + "word " * 3000 + "</p></body></html>"
    text = prd.ingest("https://docs.example.test/prd", fetch=lambda url: html)
    assert text.startswith("Vouchers") and "evil" not in text and "<p>" not in text
    assert "[cut at 8000 of" in text
    assert len(text) < 8100


@pytest.mark.parametrize(
    "text", ["Make checkout better.", "Vouchers.", "improve the thing somehow please team"]
)
def test_vague_input_asks_and_writes_nothing(tmp_path, text, capsys):
    assert prd.vague(text)
    assert prd.main(["vague", text]) == 1
    assert "too vague" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_write_refuses_an_incomplete_plan(tmp_path):
    scenarios = tmp_path / "s.json"
    scenarios.write_text(json.dumps({"scenarios": plan("main").scenarios}), encoding="utf-8")
    assert (
        prd.main(
            ["write", "--feature", "x", "--scenarios", str(scenarios), "--specs", str(tmp_path)]
        )
        == 1
    )
    assert not (tmp_path / "x.md").exists()


def test_the_command_writes_specs_first_and_commits_only_specs_on_failure():
    meta, body = read_command("generate-from-prd")
    flat = " ".join(body.split())
    assert meta["disable-model-invocation"] is True
    assert claude_md_lists("generate-from-prd")
    assert flat.index("Write the spec first") < flat.index("Classify and generate")
    for promise in (
        "ask for clarification and stop",
        "No files are written",
        "commit nothing beyond the specs",
        "3 consecutive passes",
        "8,000 characters",
    ):
        assert promise in flat, promise
