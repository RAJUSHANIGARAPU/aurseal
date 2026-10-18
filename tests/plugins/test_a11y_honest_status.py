"""The accessibility plugin earns its pass: no stub output, no invented target, no silent skip.

The old plugin wrote test files whose body was ``pass`` and returned ``pass`` itself,
defaulting to ``http://localhost`` when given nothing. These tests pin the replacement.
No browser starts: ``scan_routes`` is replaced with canned reports.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from checks._browser import BrowserUnavailable
from checks.a11y.axe import A11yReport, AxeUnavailable, Violation
from tests.plugins._tier_loader import load_module

ROUTE = "https://app.example/ui/list"


@pytest.fixture
def module():
    return load_module("tier2", "accessibility_a11y.plugin.py")


@pytest.fixture
def plugin(module):
    return module.AccessibilityA11yPlugin()


def _scanner(module, monkeypatch, reports=None, raises=None) -> list:
    calls: list = []

    def _scan(routes, policy, browser_name="chromium", **kw):  # noqa: ANN001, ANN003
        calls.append(routes)
        if raises is not None:
            raise raises
        return reports

    monkeypatch.setattr(module.runner, "scan_routes", _scan)
    return calls


def _clean(route=ROUTE) -> A11yReport:
    return A11yReport(route=route, rules_run=40, rule_ids=frozenset({"label", "image-alt"}))


def test_no_routes_is_unknown_and_nothing_is_scanned(plugin, module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _scanner(module, monkeypatch, [])
    for routes in (None, [], ["not-a-url"]):
        result = plugin.run({"routes": routes})
        assert result.status == "unknown"
        assert "localhost" not in str(result.findings)
    assert calls == []
    assert list(Path(tmp_path).rglob("*")) == [], "the plugin wrote files"


@pytest.mark.parametrize(
    "error", [AxeUnavailable("axe-playwright-python is not installed"), BrowserUnavailable("no")]
)
def test_missing_library_or_browser_is_unknown(plugin, module, monkeypatch, error):
    _scanner(module, monkeypatch, raises=error)
    assert plugin.run({"routes": [ROUTE]}).status == "unknown"


def test_dry_run_is_skip_and_scans_nothing(plugin, module, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = _scanner(module, monkeypatch, [_clean()])
    result = plugin.run({"routes": [ROUTE], "dry_run": True})
    assert result.status == "skip" and result.dry_run
    assert calls == []
    assert list(Path(tmp_path).rglob("*")) == []


def test_a_route_that_did_not_load_is_unknown(plugin, module, monkeypatch):
    _scanner(module, monkeypatch, [A11yReport(route=ROUTE, error="answered 502")])
    assert plugin.run({"routes": [ROUTE]}).status == "unknown"


def test_a_scan_that_ran_no_rules_is_unknown_not_clean(plugin, module, monkeypatch):
    _scanner(module, monkeypatch, [A11yReport(route=ROUTE, rules_run=0)])
    assert plugin.run({"routes": [ROUTE]}).status == "unknown"


def test_a_blocking_violation_fails(plugin, module, monkeypatch):
    report = _clean()
    report.violations = [Violation("label", "critical", ("wcag2a",), "Form label", 1, ("#q",))]
    _scanner(module, monkeypatch, [report])
    result = plugin.run({"routes": [ROUTE]})
    assert result.status == "fail"
    assert "label" in result.findings[0]["reason"]


def test_a_clean_scan_passes(plugin, module, monkeypatch):
    """Positive control: the plugin can still reach ``pass``."""
    _scanner(module, monkeypatch, [_clean()])
    assert plugin.run({"routes": [ROUTE]}).status == "pass"


def test_open_issue_manifest_is_honoured(plugin, module, monkeypatch, tmp_path):
    manifest = tmp_path / "a11y.manifest.json"
    manifest.write_text(
        '[{"template": "/ui/list", "open_issues": [{"issue": "BUG-9", "summary": "a11y",'
        ' "seen_in": "label rule", "finding": "label"}]}]',
        encoding="utf-8",
    )
    report = _clean()
    report.violations = [Violation("label", "serious", ("wcag2a",), "Form label", 1)]
    _scanner(module, monkeypatch, [report])
    assert plugin.run({"routes": [ROUTE], "open_issues": str(manifest)}).status == "pass"
    _scanner(module, monkeypatch, [_clean()])
    cleared = plugin.run({"routes": [ROUTE], "open_issues": str(manifest)})
    assert cleared.status == "fail" and "CLEARED" in cleared.findings[0]["reason"]


def test_no_template_generator_remains(module):
    assert not hasattr(module, "_AXE_TEMPLATE")
