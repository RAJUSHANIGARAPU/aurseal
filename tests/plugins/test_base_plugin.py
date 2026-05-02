"""Tests for BasePlugin: retry logic, dry_run, PluginResult fields."""

from __future__ import annotations

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult


class _OkPlugin(BasePlugin):
    name = "ok-plugin"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test"]

    def run(self, context: dict) -> PluginResult:
        return PluginResult(status="pass", findings=[{"ok": True}])


class _FailOncePlugin(BasePlugin):
    """Fails on first call, passes on second."""

    name = "fail-once"
    priority = PluginPriority.HIGH
    trigger_conditions = ["test"]

    def __init__(self) -> None:
        self._calls = 0

    def run(self, context: dict) -> PluginResult:
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("transient failure")
        return PluginResult(status="pass", findings=[{"calls": self._calls}])


class _AlwaysFailPlugin(BasePlugin):
    name = "always-fail"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test"]

    def run(self, context: dict) -> PluginResult:
        raise RuntimeError("permanent failure")


class _DryRunPlugin(BasePlugin):
    name = "dry-run-plugin"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test"]

    def run(self, context: dict) -> PluginResult:
        if context.get("dry_run"):
            return PluginResult(status="skip", dry_run=True)
        return PluginResult(status="pass")


class TestPluginResult:
    def test_defaults(self) -> None:
        r = PluginResult(status="pass")
        assert r.findings == []
        assert r.duration_ms == 0.0
        assert r.tokens_used == 0
        assert r.cost_usd == 0.0
        assert r.dry_run is False

    def test_with_findings(self) -> None:
        r = PluginResult(status="fail", findings=[{"key": "value"}], tokens_used=100, cost_usd=0.01)
        assert len(r.findings) == 1
        assert r.tokens_used == 100
        assert r.cost_usd == 0.01

    def test_dry_run_flag(self) -> None:
        r = PluginResult(status="skip", dry_run=True)
        assert r.dry_run is True


class TestBasePluginExecute:
    def test_ok_plugin_passes(self) -> None:
        plugin = _OkPlugin()
        result = plugin.execute({})
        assert result.status == "pass"
        assert result.duration_ms >= 0

    def test_duration_ms_populated(self) -> None:
        plugin = _OkPlugin()
        result = plugin.execute({})
        assert result.duration_ms >= 0.0

    def test_retry_on_transient_failure(self) -> None:
        plugin = _FailOncePlugin()
        # Patch sleep to avoid slowing tests
        import plugins._base_plugin as mod

        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _: None
        try:
            result = plugin.execute({})
        finally:
            mod.time.sleep = original_sleep
        assert result.status == "pass"
        assert plugin._calls == 2

    def test_exhausted_retries_returns_error(self) -> None:
        plugin = _AlwaysFailPlugin()
        import plugins._base_plugin as mod

        original_sleep = mod.time.sleep
        mod.time.sleep = lambda _: None
        try:
            result = plugin.execute({})
        finally:
            mod.time.sleep = original_sleep
        assert result.status == "error"
        assert result.findings[0].get("error")

    def test_dry_run_injects_flag(self) -> None:
        plugin = _DryRunPlugin()
        result = plugin.dry_run({})
        assert result.status == "skip"
        assert result.dry_run is True

    def test_dry_run_does_not_modify_original_context(self) -> None:
        plugin = _DryRunPlugin()
        ctx: dict = {"trigger": "test"}
        plugin.dry_run(ctx)
        assert "dry_run" not in ctx


class TestPluginPriority:
    def test_all_priorities_exist(self) -> None:
        assert PluginPriority.CRITICAL
        assert PluginPriority.HIGH
        assert PluginPriority.NORMAL
        assert PluginPriority.BACKGROUND

    def test_priority_values(self) -> None:
        assert PluginPriority.CRITICAL.value == "CRITICAL"
        assert PluginPriority.BACKGROUND.value == "BACKGROUND"
