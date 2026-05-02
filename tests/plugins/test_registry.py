"""Tests for PluginRegistry: scan, get_by_trigger, summary, validate."""

from __future__ import annotations

import pytest

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.registry import PluginRegistry


class _MockPlugin(BasePlugin):
    name = "mock-test-plugin"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test_event", "manual"]

    def run(self, context: dict) -> PluginResult:
        return PluginResult(status="pass")


class _MockCriticalPlugin(BasePlugin):
    name = "mock-critical-plugin"
    priority = PluginPriority.CRITICAL
    trigger_conditions = ["deploy"]

    def run(self, context: dict) -> PluginResult:
        return PluginResult(status="pass")


class TestPluginRegistryManual:
    """Tests that manually register plugins without scanning filesystem."""

    def setup_method(self) -> None:
        self.registry = PluginRegistry()
        # Directly insert plugins to avoid filesystem scan in unit tests
        self.registry._plugins["mock-test-plugin"] = _MockPlugin()
        self.registry._plugins["mock-critical-plugin"] = _MockCriticalPlugin()

    def test_all_returns_registered_plugins(self) -> None:
        plugins = self.registry.all()
        assert len(plugins) == 2
        names = {p.name for p in plugins}
        assert "mock-test-plugin" in names
        assert "mock-critical-plugin" in names

    def test_get_by_trigger_matches_event(self) -> None:
        plugins = self.registry.get_by_trigger("test_event")
        assert len(plugins) == 1
        assert plugins[0].name == "mock-test-plugin"

    def test_get_by_trigger_returns_empty_for_unknown(self) -> None:
        plugins = self.registry.get_by_trigger("nonexistent_event")
        assert plugins == []

    def test_get_by_trigger_manual_matches_multiple(self) -> None:
        plugins = self.registry.get_by_trigger("manual")
        assert any(p.name == "mock-test-plugin" for p in plugins)

    def test_summary_counts_total(self) -> None:
        summary = self.registry.summary()
        assert summary["total"] == 2

    def test_summary_by_priority(self) -> None:
        summary = self.registry.summary()
        assert summary["by_priority"]["NORMAL"] == 1
        assert summary["by_priority"]["CRITICAL"] == 1

    def test_summary_plugins_list(self) -> None:
        summary = self.registry.summary()
        assert "mock-test-plugin" in summary["plugins"]
        assert "mock-critical-plugin" in summary["plugins"]

    def test_validate_passes_for_valid_plugins(self) -> None:
        # Should not raise
        self.registry.validate()

    def test_validate_fails_for_empty_name(self) -> None:
        registry = PluginRegistry()

        class _BadPlugin(BasePlugin):
            name = ""
            priority = PluginPriority.NORMAL
            trigger_conditions = ["test"]

            def run(self, context: dict) -> PluginResult:
                return PluginResult(status="pass")

        registry._plugins[""] = _BadPlugin()
        with pytest.raises(AssertionError):
            registry.validate()

    def test_validate_fails_for_empty_triggers(self) -> None:
        registry = PluginRegistry()

        class _NoTriggerPlugin(BasePlugin):
            name = "no-trigger"
            priority = PluginPriority.NORMAL
            trigger_conditions: list[str] = []

            def run(self, context: dict) -> PluginResult:
                return PluginResult(status="pass")

        registry._plugins["no-trigger"] = _NoTriggerPlugin()
        with pytest.raises(AssertionError):
            registry.validate()


class TestPluginRegistryScan:
    """Tests that scan the actual filesystem for .plugin.py files."""

    def test_scan_discovers_plugins(self) -> None:
        registry = PluginRegistry()
        registry.scan()
        # Should find at least some plugins from our tier directories
        assert registry.summary()["total"] > 0

    def test_scan_all_have_names(self) -> None:
        registry = PluginRegistry()
        registry.scan()
        for plugin in registry.all():
            assert plugin.name, f"Plugin {type(plugin).__name__} has empty name"

    def test_scan_all_have_triggers(self) -> None:
        registry = PluginRegistry()
        registry.scan()
        for plugin in registry.all():
            assert plugin.trigger_conditions, f"Plugin {plugin.name} has empty trigger_conditions"

    def test_scan_finds_tier1_unit_ai(self) -> None:
        registry = PluginRegistry()
        registry.scan()
        names = [p.name for p in registry.all()]
        assert "unit-ai" in names

    def test_scan_finds_tier4_plugins(self) -> None:
        registry = PluginRegistry()
        registry.scan()
        names = [p.name for p in registry.all()]
        assert any("temporal" in n or "synthetic" in n or "state-machine" in n for n in names)
