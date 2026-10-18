"""The security plugin can fail, and cannot pass without every tool having answered.

No tool runs: ``checks.security.scan.run_tool`` is replaced with canned runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from checks.security import scan as scan_module
from checks.security import tools
from checks.security.model import SecurityFinding, Severity, ToolRun
from tests.plugins._tier_loader import load_module

FIXTURES = Path(__file__).resolve().parents[1] / "checks" / "fixtures"


@pytest.fixture
def plugin():
    return load_module("tier2", "security_scan.plugin.py").SecurityScanPlugin()


@pytest.fixture
def allowlist(tmp_path) -> str:
    path = tmp_path / "allow.yml"
    path.write_text("entries: []\n", encoding="utf-8")
    return str(path)


def _tools(monkeypatch, **runs: ToolRun) -> list:
    seen: list = []

    def run(name, **kwargs):  # noqa: ANN001, ANN003
        seen.append((name, kwargs))
        return runs.get(name.replace("-", "_")) or ToolRun(name, ran=False, reason="not installed")

    monkeypatch.setattr(scan_module, "run_tool", run)
    return seen


def test_a_missing_tool_is_unknown_not_pass(plugin, monkeypatch, allowlist):
    _tools(monkeypatch, bandit=ToolRun("bandit", ran=True), pip_audit=ToolRun("pip-audit", True))
    result = plugin.run({"allowlist": allowlist})
    assert result.status == "unknown"
    assert "gitleaks" in result.findings[0]["reason"]


def test_a_cve_fails(plugin, monkeypatch, allowlist):
    import json

    vulns = tools.parse_pip_audit(
        json.loads((FIXTURES / "pip-audit-report.json").read_text(encoding="utf-8"))
    )
    _tools(monkeypatch, pip_audit=ToolRun("pip-audit", ran=True, findings=vulns))
    result = plugin.run({"tools": ["pip-audit"], "allowlist": allowlist})
    assert result.status == "fail"


def test_a_secret_fails(plugin, monkeypatch, allowlist):
    leak = SecurityFinding("gitleaks", "generic-api-key", Severity.HIGH, "a.py:1", "gitleaks:x")
    _tools(monkeypatch, gitleaks=ToolRun("gitleaks", ran=True, findings=[leak]))
    assert plugin.run({"tools": ["gitleaks"], "allowlist": allowlist}).status == "fail"


def test_clean_only_when_every_tool_ran(plugin, monkeypatch, allowlist):
    """Positive control."""
    _tools(
        monkeypatch,
        gitleaks=ToolRun("gitleaks", ran=True),
        bandit=ToolRun("bandit", ran=True),
        pip_audit=ToolRun("pip-audit", ran=True),
    )
    assert plugin.run({"allowlist": allowlist}).status == "pass"


def test_dry_run_is_skip_and_starts_nothing(plugin, monkeypatch):
    seen = _tools(monkeypatch)
    result = plugin.run({"dry_run": True})
    assert result.status == "skip" and seen == []


def test_a_broken_allowlist_is_an_error(plugin, monkeypatch, tmp_path):
    bad = tmp_path / "allow.yml"
    bad.write_text("entries:\n  - fingerprint: x\n", encoding="utf-8")
    _tools(monkeypatch)
    assert plugin.run({"allowlist": str(bad)}).status == "error"


def test_the_source_walk_excludes_tests_and_venv(plugin, monkeypatch, allowlist, tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "__init__.py").write_text("")
    seen = _tools(monkeypatch, bandit=ToolRun("bandit", ran=True))
    plugin.run({"tools": ["bandit"], "source_dir": str(tmp_path), "allowlist": allowlist})
    assert seen[0][1]["root"] == tmp_path
    assert tools.default_bandit_targets(tmp_path) == [tmp_path / "pkg"]


def test_the_regex_grep_is_gone():
    source = Path(__file__).resolve().parents[2] / "plugins" / "tier2" / "security_scan.plugin.py"
    text = source.read_text(encoding="utf-8")
    assert "_SECRET_PATTERNS" not in text and "rglob" not in text
