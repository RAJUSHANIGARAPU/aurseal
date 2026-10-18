"""The generator command line: spec, graph, generate and convert, and the never-overwrite rule.

Model calls go through ``tests.core._fake_claude.FakeClaude``, which replaces only
``requests.post``: prompt assembly, response parsing and the write guard run for real.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from generators import cli
from tests.core._fake_claude import FakeClaude
from traceability.review import unmark_draft

MACHINE = Path(__file__).resolve().parents[2] / "demo_sut" / "config" / "subscription.machine.yaml"

PAGE = "class SubscriptionPage:\n    pass\n"
TEST = "def test_generated():\n    assert True\n"
WORKFLOW_COMPLETION = (
    f"===PAGE_OBJECT_START===\n{PAGE}===PAGE_OBJECT_END===\n"
    f"===TEST_START===\n```python\n{TEST}```\n===TEST_END===\n"
)
LOCATORS = {"save": {"type": "data", "attribute": "data-action", "value": "save"}}
RECORDING_COMPLETION = WORKFLOW_COMPLETION + (
    f"===LOCATORS_START===\n{json.dumps(LOCATORS)}\n===LOCATORS_END===\n"
)
RECORDING = """from playwright.sync_api import sync_playwright

def run(page):
    page.goto("http://127.0.0.1:8765/ui/orders/new")
    page.locator("[data-action=save]").click()
"""
EXISTING = "# hand-written, must survive a failed generation\n"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return tmp_path


def generate(*extra):
    return cli.main(["generate", str(MACHINE), *extra])


def test_spec_writes_the_spec_and_prints_next_steps(workdir, capsys):
    assert cli.main(["spec", str(MACHINE), "--out", "out"]) == 0
    assert (workdir / "out" / "subscription.md").read_text().startswith("---\nseed: false")
    assert "next steps:" in capsys.readouterr().out


def test_graph_prints_the_graph_and_fails_on_inconsistency(workdir, capsys):
    assert cli.main(["graph", str(MACHINE)]) == 0
    out = capsys.readouterr().out
    assert "unreachable: suspended" in out and "paths: " in out
    broken = workdir / "broken.yaml"
    broken.write_text(
        "key: x\ninitial_states: [a]\nstates:\n  a:\n    transitions: [{action: go, to: b}]\n"
    )
    assert cli.main(["graph", str(broken)]) == 1
    assert "problem:" in capsys.readouterr().out


def test_a_file_that_is_not_a_workflow_exits_2(workdir, capsys):
    other = workdir / "other.yaml"
    other.write_text("timeout: 3\n")
    assert cli.main(["spec", str(other)]) == 2
    assert "error:" in capsys.readouterr().out


def test_generate_runs_the_pipeline_and_writes_page_and_test(workdir, monkeypatch, capsys):
    fake = FakeClaude(WORKFLOW_COMPLETION).install(monkeypatch)
    assert generate() == 0
    assert (workdir / "specs" / "subscription.md").exists()
    assert (workdir / "ui" / "pages" / "subscription_page.py").read_text() == PAGE
    assert (
        unmark_draft((workdir / "ui" / "tests" / "test_subscription_generated.py").read_text())
        == TEST
    )
    assert "## Scenario create a subscription" in fake.prompt  # the spec reached the model
    assert "data-testid" in fake.prompt  # the selector priority reached it too
    assert "next steps:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "fake",
    [
        FakeClaude(error=RuntimeError("upstream 529")),
        FakeClaude("===PAGE_OBJECT_START===\nonly half\n===PAGE_OBJECT_END===\n"),
        FakeClaude("Sorry, I cannot do that."),
    ],
    ids=["api-error", "missing-test-section", "no-sections"],
)
def test_a_failed_generation_writes_nothing_and_keeps_existing_files(workdir, monkeypatch, fake):
    fake.install(monkeypatch)
    target = workdir / "ui" / "tests" / "test_subscription_generated.py"
    target.parent.mkdir(parents=True)
    target.write_text(EXISTING)
    assert generate() == 1
    assert target.read_text() == EXISTING
    assert not (workdir / "ui" / "pages" / "subscription_page.py").exists()


def test_without_an_api_key_nothing_is_generated(workdir, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    fake = FakeClaude(WORKFLOW_COMPLETION).install(monkeypatch)
    assert generate() == 1
    assert fake.prompts == []


def test_the_wait_is_bounded_and_a_timeout_writes_nothing(workdir, capsys):
    class Slow:
        def generate_from_workflow(self, spec_path, key):
            time.sleep(2)
            return None

    args = cli.build_parser().parse_args(["generate", str(MACHINE), "--timeout", "0.2"])
    started = time.monotonic()
    assert cli.cmd_generate(args, generator=Slow()) == 1
    assert time.monotonic() - started < 1.5
    assert "did not finish within" in capsys.readouterr().out
    assert not (workdir / "ui").exists()


def test_bounded_reports_progress_while_waiting(capsys):
    def slow():
        time.sleep(0.25)
        return "done"

    assert cli.bounded(slow, timeout_s=2, progress_s=0.1, label="x") == "done"
    assert "still running" in capsys.readouterr().out


def test_convert_writes_page_test_and_validated_locators(workdir, monkeypatch, capsys):
    recording = workdir / "orders.py"
    recording.write_text(RECORDING)
    fake = FakeClaude(RECORDING_COMPLETION).install(monkeypatch)
    assert cli.main(["convert", str(recording), "--name", "orders"]) == 0
    assert (workdir / "ui" / "pages" / "orders_page.py").read_text() == PAGE
    assert unmark_draft((workdir / "ui" / "tests" / "test_orders.py").read_text()) == TEST
    assert (
        json.loads((workdir / "ui" / "locators" / "orders_locators.json").read_text()) == LOCATORS
    )
    assert 'page.locator("[data-action=save]")' in fake.prompt
    assert "data-* test attribute > data-testid" in fake.prompt
    assert "next steps:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "locators",
    ['{"save": {"type": "xpath", "value": "//button"}}', "not json", "{}"],
    ids=["unrenderable-entry", "not-json", "empty"],
)
def test_convert_refuses_an_unusable_locator_registry(workdir, monkeypatch, locators):
    recording = workdir / "orders.ts"
    recording.write_text("await page.click('text=Save');\n")
    completion = WORKFLOW_COMPLETION + f"===LOCATORS_START===\n{locators}\n===LOCATORS_END===\n"
    FakeClaude(completion).install(monkeypatch)
    assert cli.convert_recording(recording, "orders") is None
    assert not (workdir / "ui").exists()


def test_convert_of_a_missing_recording_makes_no_call(workdir, monkeypatch):
    fake = FakeClaude(RECORDING_COMPLETION).install(monkeypatch)
    assert cli.main(["convert", "missing.py", "--name", "orders"]) == 1
    assert fake.prompts == []
