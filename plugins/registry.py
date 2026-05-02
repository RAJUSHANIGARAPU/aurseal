"""Plugin registry — auto-discovers and manages BasePlugin subclasses."""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority

logger = logging.getLogger(__name__)

_PLUGINS_ROOT = Path(__file__).parent


class PluginRegistry:
    """Discovers, registers, and dispatches plugins by trigger event."""

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}

    def scan(self, plugins_dir: Path | None = None) -> None:
        """Walk plugins_dir (default: plugins/) for *.plugin.py and register subclasses."""
        root = plugins_dir or _PLUGINS_ROOT
        for plugin_file in sorted(root.rglob("*.plugin.py")):
            self._load_file(plugin_file)

    def _load_file(self, path: Path) -> None:
        module_name = path.stem.replace(".", "_") + "_" + str(path.parent.name)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load plugin file %s: %s", path, exc)
            return
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BasePlugin) and obj is not BasePlugin and getattr(obj, "name", ""):
                instance = obj()
                self._plugins[instance.name] = instance
                logger.info("Registered plugin: %s (priority=%s)", instance.name, instance.priority)

    def get_by_trigger(self, event: str) -> list[BasePlugin]:
        """Return all plugins whose trigger_conditions contains the event."""
        return [p for p in self._plugins.values() if event in (p.trigger_conditions or [])]

    def all(self) -> list[BasePlugin]:
        """Return all registered plugins."""
        return list(self._plugins.values())

    def summary(self) -> dict:
        by_priority: dict[str, int] = {p.value: 0 for p in PluginPriority}
        for plugin in self._plugins.values():
            key = getattr(plugin.priority, "value", str(plugin.priority))
            by_priority[key] = by_priority.get(key, 0) + 1
        return {
            "total": len(self._plugins),
            "by_priority": by_priority,
            "plugins": [p.name for p in self._plugins.values()],
        }

    def validate(self) -> None:
        """Assert all registered plugins have name, priority, and trigger_conditions."""
        for plugin in self._plugins.values():
            assert plugin.name, f"Plugin {type(plugin).__name__} has empty name"
            assert plugin.priority, f"Plugin {plugin.name} has no priority"
            assert plugin.trigger_conditions, f"Plugin {plugin.name} has empty trigger_conditions"
