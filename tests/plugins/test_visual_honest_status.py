"""The visual plugin never writes an empty baseline and never passes without comparing.

No browser starts: ``snapshot_routes`` is replaced with canned outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from checks._browser import BrowserUnavailable
from checks._outcome import CheckOutcome
from checks.visual.compare import PillowUnavailable
from tests.plugins._tier_loader import load_module

ROUTE = "https://app.example/ui/home"


@pytest.fixture
def module():
    return load_module("tier2", "visual_regression.plugin.py")


@pytest.fixture
def plugin(module):
    return module.VisualRegressionPlugin()


def _runner(module, monkeypatch, outcomes=None, raises=None) -> list:
    calls: list = []

    def _snap(routes, store, **kw):  # noqa: ANN001, ANN003
        calls.append((routes, store, kw))
        if raises is not None:
            raise raises
        return outcomes

    monkeypatch.setattr(module.runner, "snapshot_routes", _snap)
    return calls


def test_no_routes_is_unknown_and_writes_nothing(plugin, module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _runner(module, monkeypatch, [])
    for routes in (None, [], ["relative/path"], {}):
        result = plugin.run({"routes": routes})
        assert result.status == "unknown"
        assert "localhost" not in str(result.findings)
    assert calls == []
    assert list(Path(tmp_path).rglob("*")) == []


def test_routes_get_names_from_their_path(module):
    assert module._named_routes(["https://a.example/ui/home", "https://a.example/"]) == {
        "ui-home": "https://a.example/ui/home",
        "root": "https://a.example/",
    }
    assert module._named_routes({"home": ROUTE, "../x": ROUTE}) == {"home": ROUTE}


@pytest.mark.parametrize("error", [BrowserUnavailable("no"), PillowUnavailable("no pillow")])
def test_missing_browser_or_pillow_is_unknown(plugin, module, monkeypatch, error):
    _runner(module, monkeypatch, raises=error)
    assert plugin.run({"routes": [ROUTE]}).status == "unknown"


def test_a_missing_baseline_is_not_a_pass(plugin, module, monkeypatch):
    _runner(module, monkeypatch, [CheckOutcome.unknown("visual", "no baseline: nothing compared")])
    assert plugin.run({"routes": [ROUTE]}).status == "unknown"


def test_taking_baselines_is_skip_and_is_asked_for(plugin, module, monkeypatch):
    calls = _runner(
        module,
        monkeypatch,
        [CheckOutcome.skipped("visual", "baseline taken", baseline_taken=True)],
    )
    result = plugin.run({"routes": [ROUTE], "update_baselines": True})
    assert result.status == "skip"
    assert result.findings[0]["baseline_taken"] is True
    assert calls[0][2]["update"] is True


def test_a_mismatch_fails(plugin, module, monkeypatch):
    _runner(
        module,
        monkeypatch,
        [CheckOutcome.ok("visual", "a"), CheckOutcome.breached("visual", "home: mismatch")],
    )
    assert plugin.run({"routes": [ROUTE]}).status == "fail"


def test_all_matching_passes(plugin, module, monkeypatch):
    """Positive control: the plugin can still reach ``pass``."""
    _runner(module, monkeypatch, [CheckOutcome.ok("visual", "home: match")])
    assert plugin.run({"routes": [ROUTE]}).status == "pass"


def test_dry_run_is_skip_and_starts_nothing(plugin, module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _runner(module, monkeypatch, [])
    result = plugin.run({"routes": [ROUTE], "dry_run": True})
    assert result.status == "skip" and calls == []
    assert list(Path(tmp_path).rglob("*")) == []


def test_baselines_are_anchored_to_the_repo_not_the_cwd(plugin, module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _runner(module, monkeypatch, [CheckOutcome.ok("visual", "x")])
    plugin.run({"routes": [ROUTE]})
    store = calls[0][1]
    assert store.root == Path(module.__file__).resolve().parents[2] / "visual_baselines"


def test_the_empty_baseline_writer_is_gone(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert 'write_bytes(b"")' not in source
