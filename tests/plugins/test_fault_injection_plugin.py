"""The fault-injection plugin reports what real chaos tests established, and nothing more.

It replaced chaos-resilience, whose ``pass`` meant "the model replied". pytest is never
started here: ``subprocess.run`` is replaced and a JUnit file is written as pytest would.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.plugins._tier_loader import load_module

PLUGINS = Path(__file__).resolve().parents[2] / "plugins"


@pytest.fixture
def module():
    return load_module("tier3", "fault_injection.plugin.py")


@pytest.fixture
def plugin(module):
    return module.FaultInjectionPlugin()


def _pytest(monkeypatch, code: int, tests=0, failures=0, skipped=0, write=True) -> list:
    calls: list = []

    def run(cmd, **kw):  # noqa: ANN001, ANN003
        calls.append(cmd)
        junit = next(a.split("=", 1)[1] for a in cmd if a.startswith("--junitxml="))
        if write:
            Path(junit).write_text(
                f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="0" '
                f'skipped="{skipped}"/></testsuites>',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, code, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_the_prose_only_chaos_plugin_is_gone():
    assert not (PLUGINS / "tier3" / "chaos_resilience.plugin.py").exists()


def test_passing_chaos_tests_pass(plugin, monkeypatch):
    calls = _pytest(monkeypatch, 0, tests=3)
    result = plugin.run({})
    assert result.status == "pass"
    assert calls[0][calls[0].index("-m", 3) + 1] == "chaos"


def test_all_skipped_is_unknown_not_pass(plugin, monkeypatch):
    """Every test skipped = no fault ever fired (held_empty): nothing was verified."""
    _pytest(monkeypatch, 0, tests=3, skipped=3)
    result = plugin.run({})
    assert result.status == "unknown"
    assert "no fault was exercised" in result.findings[0]["reason"]


@pytest.mark.parametrize(
    ("code", "status"), [(1, "fail"), (2, "error"), (4, "error"), (5, "unknown"), (99, "unknown")]
)
def test_pytest_exit_codes(plugin, monkeypatch, code, status):
    _pytest(monkeypatch, code, tests=2, failures=1 if code == 1 else 0)
    assert plugin.run({}).status == status


def test_a_timeout_is_unknown(plugin, monkeypatch):
    def run(cmd, **kw):  # noqa: ANN001, ANN003
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", run)
    assert plugin.run({}).status == "unknown"


def test_dry_run_starts_nothing(plugin, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("started"))
    result = plugin.run({"dry_run": True})
    assert result.status == "skip" and result.dry_run


def test_the_registry_loads_it(module):
    from plugins.registry import PluginRegistry

    registry = PluginRegistry()
    registry.scan()
    names = registry.summary()["plugins"]
    assert "fault-injection" in names and "chaos-resilience" not in names
    assert not registry.load_failures
