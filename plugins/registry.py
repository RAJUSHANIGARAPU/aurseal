"""Plugin registry — auto-discovers and manages BasePlugin subclasses.

A plugin that cannot be loaded used to leave no trace an caller could act on. It
was logged at WARNING and dropped, and since ``MasterOrchestrator`` builds its
tiers *from this registry*, the plugin then vanished from the health score's
denominator as well — so a run that could not load a CRITICAL plugin scored on
the survivors alone, and with a small enough tier that is a full-marks deploy.

Scoring the plugins that were *expected* rather than the ones that reported does
not reach this on its own, because a plugin that never loaded was never expected
either. Something has to survive the failure, so ``load_failures`` records it.

The name and priority of a plugin inside an unimportable file are unknowable —
that is what "it did not import" means — so this cannot be repaired by scoring
it as a failure in its tier. What it can do is prevent the run being called
healthy, which is the honest answer: a run that could not load part of its own
suite does not know whether the product is well.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority

logger = logging.getLogger(__name__)

_PLUGINS_ROOT = Path(__file__).parent


@dataclass(frozen=True)
class LoadFailure:
    """A plugin file that did not yield a usable plugin, and why."""

    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


class PluginRegistry:
    """Discovers, registers, and dispatches plugins by trigger event."""

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self.load_failures: list[LoadFailure] = []

    def scan(self, plugins_dir: Path | None = None) -> None:
        """Walk plugins_dir (default: plugins/) for *.plugin.py and register subclasses."""
        root = plugins_dir or _PLUGINS_ROOT
        for plugin_file in sorted(root.rglob("*.plugin.py")):
            self._load_file(plugin_file)

    def _record_failure(self, path: Path, reason: str) -> None:
        logger.warning("Failed to load plugin file %s: %s", path, reason)
        self.load_failures.append(LoadFailure(path=str(path), reason=reason))

    def _load_file(self, path: Path) -> None:
        module_name = path.stem.replace(".", "_") + "_" + str(path.parent.name)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            self._record_failure(path, "no import spec could be built for this file")
            return
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self._record_failure(path, f"{type(exc).__name__}: {exc}")
            return
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BasePlugin) and obj is not BasePlugin and getattr(obj, "name", ""):
                # Instantiation was outside the try, so one plugin raising in
                # __init__ — or an abstract subclass missing run() — aborted the
                # whole scan and every file sorted after it never loaded.
                try:
                    instance = obj()
                except Exception as exc:  # noqa: BLE001
                    self._record_failure(
                        path, f"{obj.__name__}() raised {type(exc).__name__}: {exc}"
                    )
                    continue
                existing = self._plugins.get(instance.name)
                if existing is not None:
                    # Last file in sorted() order used to win silently, so a
                    # CRITICAL plugin could be replaced by a NORMAL namesake and
                    # never run, never report, and never be missed.
                    self._record_failure(
                        path,
                        f"duplicate plugin name {instance.name!r}, already registered by "
                        f"{type(existing).__name__}",
                    )
                    continue
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
