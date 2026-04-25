"""Tests for memory.pytest_plugin.MemoryPlugin."""

from __future__ import annotations

from unittest.mock import MagicMock

from memory.config import MemoryConfig
from memory.pytest_plugin import MemoryPlugin
from memory.summarizer import MemorySummarizer


def _config(mode: str = "passive") -> MemoryConfig:
    return MemoryConfig(enabled=True, mode=mode, ttl_days=90)


def _plugin(mode: str = "passive") -> tuple[MemoryPlugin, MagicMock]:
    store = MagicMock()
    summarizer = MemorySummarizer()
    plugin = MemoryPlugin(store=store, summarizer=summarizer, config=_config(mode))
    return plugin, store


def _report(
    when: str = "call",
    passed: bool = True,
    failed: bool = False,
    nodeid: str = "tests/test_foo.py::test_bar",
    duration: float = 1.0,
) -> MagicMock:
    r = MagicMock()
    r.when = when
    r.passed = passed
    r.failed = failed
    r.nodeid = nodeid
    r.duration = duration
    r.longrepr = "AssertionError: check failed" if failed else ""
    return r


# ------------------------------------------------------------------
# Hook filtering
# ------------------------------------------------------------------


def test_plugin_ignores_setup_phase():
    plugin, store = _plugin()
    plugin.pytest_runtest_logreport(_report(when="setup", passed=True))
    store.save.assert_not_called()


def test_plugin_ignores_teardown_phase():
    plugin, store = _plugin()
    plugin.pytest_runtest_logreport(_report(when="teardown", passed=True))
    store.save.assert_not_called()


def test_plugin_ignores_passed_in_passive_mode():
    plugin, store = _plugin(mode="passive")
    plugin.pytest_runtest_logreport(_report(when="call", passed=True, failed=False))
    store.save.assert_not_called()


def test_plugin_stores_failed_in_passive_mode():
    plugin, store = _plugin(mode="passive")
    plugin.pytest_runtest_logreport(_report(when="call", passed=False, failed=True))
    store.save.assert_called_once()


def test_plugin_stores_passed_in_active_mode():
    plugin, store = _plugin(mode="active")
    plugin.pytest_runtest_logreport(_report(when="call", passed=True, failed=False))
    store.save.assert_called_once()


def test_plugin_marks_resolved_on_pass_in_active_mode():
    plugin, store = _plugin(mode="active")
    plugin.pytest_runtest_logreport(
        _report(when="call", passed=True, failed=False, nodeid="tests/test_foo.py::test_bar")
    )
    store.update_outcome.assert_called_once_with("tests/test_foo.py::test_bar", "resolved")


def test_plugin_does_not_mark_resolved_in_passive_mode():
    plugin, store = _plugin(mode="passive")
    plugin.pytest_runtest_logreport(_report(when="call", passed=False, failed=True))
    store.update_outcome.assert_not_called()


# ------------------------------------------------------------------
# Record content
# ------------------------------------------------------------------


def test_stored_record_has_correct_node_id():
    plugin, store = _plugin()
    plugin.pytest_runtest_logreport(
        _report(when="call", passed=False, failed=True, nodeid="tests/test_x.py::test_y")
    )
    saved_record = store.save.call_args[0][0]
    assert saved_record.test_id == "tests/test_x.py::test_y"


def test_stored_record_category_is_failure():
    plugin, store = _plugin()
    plugin.pytest_runtest_logreport(_report(when="call", passed=False, failed=True))
    saved_record = store.save.call_args[0][0]
    assert saved_record.category == "failure"


# ------------------------------------------------------------------
# session finish
# ------------------------------------------------------------------


def test_sessionfinish_calls_prune():
    plugin, store = _plugin()
    store.prune_expired.return_value = 0
    plugin.pytest_sessionfinish(session=MagicMock(), exitstatus=0)
    store.prune_expired.assert_called_once()


# ------------------------------------------------------------------
# from_config factory
# ------------------------------------------------------------------


def test_from_config_returns_plugin_instance(tmp_path):
    cfg = MemoryConfig(enabled=True, db_path=tmp_path / "mem.db")
    plugin = MemoryPlugin.from_config(cfg)
    assert isinstance(plugin, MemoryPlugin)
    plugin._store.close()
