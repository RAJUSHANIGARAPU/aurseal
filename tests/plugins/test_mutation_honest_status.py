"""The mutation plugin: no 2.x flags, no ``warn`` for survivors, no ``skip`` for a missing tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from checks._outcome import CheckOutcome
from checks.mutation import mutmut
from tests.plugins._tier_loader import load_module


@pytest.fixture
def module():
    return load_module("tier2", "mutation_testing.plugin.py")


@pytest.fixture
def plugin(module):
    return module.MutationTestingPlugin()


def test_missing_mutmut_is_unknown_not_skip(plugin, monkeypatch):
    monkeypatch.setattr(mutmut, "mutmut_version", lambda: None)
    result = plugin.run({"packages": ["checks"]})
    assert result.status == "unknown"
    assert "not installed" in result.findings[0]["reason"]


def test_dry_run_is_skip_not_pass(plugin, module, monkeypatch):
    monkeypatch.setattr(module.runner, "run_mutation", lambda *a, **k: pytest.fail("started"))
    result = plugin.run({"dry_run": True})
    assert result.status == "skip" and result.dry_run


def test_survivors_below_threshold_fail(plugin, module, monkeypatch):
    monkeypatch.setattr(
        module.runner,
        "run_mutation",
        lambda *a, **k: CheckOutcome.breached("mutation", "below threshold: checks 40% < 47%"),
    )
    assert plugin.run({"packages": ["checks"]}).status == "fail"


def test_a_measured_pass_passes(plugin, module, monkeypatch):
    """Positive control."""
    monkeypatch.setattr(
        module.runner, "run_mutation", lambda *a, **k: CheckOutcome.ok("mutation", "ok")
    )
    assert plugin.run({"packages": ["checks"]}).status == "pass"


def test_a_bad_config_is_an_error(plugin, tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text("[tool.wiki_checks.mutation]\n", encoding="utf-8")
    assert plugin.run({"config": str(bad)}).status == "error"
    assert plugin.run({"packages": ["no-such-package"]}).status == "error"


def test_no_mutmut_2x_flags_remain(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]  # skip the module docstring, which names them
    assert "--paths-to-mutate" not in code and "--disable-mutation-types" not in code
    assert '"warn"' not in code
