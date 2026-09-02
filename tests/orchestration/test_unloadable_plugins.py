"""
A plugin that never loads must not be invisible to the deploy gate.

These tests go through the REAL path: plugin files written to disk, a real
``PluginRegistry.scan()``, and ``MasterOrchestrator.run()``. That distinction is
the whole point of this file.

The health score was already fixed once, to score the plugins *expected* in a
tier rather than the ones that reported. The test covering it built the tier
list by hand and passed. But ``run()`` builds its tiers **from the registry**,
and the registry drops a plugin it could not import — so a plugin that never
loaded was never expected either, the fix could not see it, and the hand-built
input could not occur in a real run. The test was shaped around the function
instead of around what the caller can actually hand it.

So: write the broken plugin file, scan it for real, and read the deploy verdict
that comes out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestration.master_orchestrator import MasterOrchestrator

WORKING = '''
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult


class Working(BasePlugin):
    name = "working"
    priority = PluginPriority.{priority}
    trigger_conditions = ["manual"]

    def run(self, context):
        return PluginResult(status="pass", findings=[], cost_usd=0.0)
'''

UNIMPORTABLE = """
import a_dependency_that_is_not_installed  # noqa: F401

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult


class Broken(BasePlugin):
    name = "broken"
    priority = PluginPriority.CRITICAL
    trigger_conditions = ["manual"]

    def run(self, context):
        return PluginResult(status="fail", findings=[], cost_usd=0.0)
"""

RAISES_IN_INIT = """
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult


class Exploding(BasePlugin):
    name = "exploding"
    priority = PluginPriority.HIGH
    trigger_conditions = ["manual"]

    def __init__(self):
        raise RuntimeError("cannot construct")

    def run(self, context):
        return PluginResult(status="pass", findings=[], cost_usd=0.0)
"""


def _write(directory: Path, filename: str, source: str) -> None:
    (directory / filename).write_text(source)


def _run(plugins_dir: Path, tmp_path: Path) -> dict:
    """Run the orchestrator against a directory of plugin files, for real."""
    orchestrator = MasterOrchestrator(budget_usd=1.0)
    orchestrator._registry._plugins.clear()
    orchestrator._registry.load_failures.clear()
    orchestrator._registry.scan(plugins_dir)
    orchestrator._storage._db_path = tmp_path / "runs.db"  # noqa: SLF001
    return orchestrator.run({"trigger": "manual", "dry_run": True})


class TestAPluginThatDidNotLoadBlocksTheDeploy:
    def test_an_unimportable_plugin_withholds_deploy(self, tmp_path):
        """
        The case the previous fix was written for, driven the way it actually
        happens. Before this change the run scored 100 and deployed.
        """
        _write(tmp_path, "working.plugin.py", WORKING.format(priority="NORMAL"))
        _write(tmp_path, "broken.plugin.py", UNIMPORTABLE)

        result = _run(tmp_path, tmp_path)

        assert result["deploy"] is False

    def test_the_failure_is_named_in_the_result(self, tmp_path):
        """A refusal nobody can explain gets overridden. Say which file."""
        _write(tmp_path, "broken.plugin.py", UNIMPORTABLE)

        result = _run(tmp_path, tmp_path)

        failures = result["summary"]["load_failures"]
        assert len(failures) == 1
        assert "broken.plugin.py" in failures[0]
        assert "a_dependency_that_is_not_installed" in failures[0]

    def test_a_plugin_raising_in_init_is_recorded_not_fatal(self, tmp_path):
        """
        Instantiation sat outside the try, so one bad __init__ aborted the whole
        scan and every file sorted after it silently never loaded.
        """
        _write(tmp_path, "a_exploding.plugin.py", RAISES_IN_INIT)
        _write(tmp_path, "z_working.plugin.py", WORKING.format(priority="NORMAL"))

        result = _run(tmp_path, tmp_path)

        assert "working" in result["summary"]["plugins_run"]
        assert result["deploy"] is False

    def test_a_duplicate_name_does_not_silently_replace(self, tmp_path):
        """
        `self._plugins[name] = instance` let the last file in sorted order win,
        so a CRITICAL plugin could be replaced by a NORMAL namesake and never
        run, never report, and never be missed.
        """
        _write(tmp_path, "a_first.plugin.py", WORKING.format(priority="CRITICAL"))
        _write(tmp_path, "z_second.plugin.py", WORKING.format(priority="NORMAL"))

        result = _run(tmp_path, tmp_path)

        assert any("duplicate" in f for f in result["summary"]["load_failures"])
        assert result["deploy"] is False


class TestAHealthyRunStillDeploys:
    """
    Positive controls. Withholding deploy unconditionally would satisfy every
    test above, so these must pass on both sides of the change.
    """

    def test_a_clean_run_deploys(self, tmp_path):
        _write(tmp_path, "working.plugin.py", WORKING.format(priority="NORMAL"))

        result = _run(tmp_path, tmp_path)

        assert result["deploy"] is True
        assert result["health_score"] == 100

    def test_a_clean_run_records_no_load_failures(self, tmp_path):
        _write(tmp_path, "working.plugin.py", WORKING.format(priority="NORMAL"))

        assert _run(tmp_path, tmp_path)["summary"]["load_failures"] == []

    def test_the_working_plugin_actually_ran(self, tmp_path):
        """
        Control for the control: if nothing ran, `deploy is True` above would be
        measuring an empty run rather than a healthy one.
        """
        _write(tmp_path, "working.plugin.py", WORKING.format(priority="NORMAL"))

        result = _run(tmp_path, tmp_path)

        assert result["summary"]["statuses"] == {"working": "pass"}


class TestTheRegistryRecordsWhatItDropped:
    def test_scan_survives_an_unimportable_file(self, tmp_path):
        from plugins.registry import PluginRegistry

        _write(tmp_path, "broken.plugin.py", UNIMPORTABLE)
        _write(tmp_path, "working.plugin.py", WORKING.format(priority="NORMAL"))

        registry = PluginRegistry()
        registry.scan(tmp_path)

        assert [p.name for p in registry.all()] == ["working"]
        assert len(registry.load_failures) == 1

    def test_a_fresh_registry_starts_clean(self):
        from plugins.registry import PluginRegistry

        assert PluginRegistry().load_failures == []

    @pytest.mark.parametrize("source", [UNIMPORTABLE, RAISES_IN_INIT])
    def test_the_reason_carries_the_underlying_error(self, tmp_path, source):
        from plugins.registry import PluginRegistry

        _write(tmp_path, "broken.plugin.py", source)
        registry = PluginRegistry()
        registry.scan(tmp_path)

        reason = registry.load_failures[0].reason
        assert "Error" in reason or "error" in reason or "not installed" in reason
