"""Tests for PluginStorage: save_run, save_plugin_result, get_recent_runs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestration.storage import PluginStorage
from plugins._base_plugin import PluginResult


@pytest.fixture
def tmp_storage(tmp_path: Path) -> PluginStorage:
    db_path = tmp_path / "test_plugin_runs.db"
    return PluginStorage(db_path=db_path)


class TestPluginStorageInit:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "plugin_runs.db"
        storage = PluginStorage(db_path=db_path)
        assert db_path.exists()
        storage.close()

    def test_tables_created(self, tmp_storage: PluginStorage) -> None:
        cur = tmp_storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert "test_run_summary" in tables
        assert "plugin_result" in tables
        assert "finding" in tables
        assert "cost_record" in tables
        assert "edge_case_node" in tables
        assert "golden_snapshot" in tables
        assert "compliance_check" in tables


class TestSaveRun:
    def test_save_and_retrieve_run(self, tmp_storage: PluginStorage) -> None:
        tmp_storage.save_run("run-001", 85, ["unit-ai", "security-scan"], 0.05)
        runs = tmp_storage.get_recent_runs(n=5)
        assert len(runs) == 1
        assert runs[0]["id"] == "run-001"
        assert runs[0]["health_score"] == 85
        assert runs[0]["plugins_run"] == ["unit-ai", "security-scan"]
        assert runs[0]["cost_usd"] == pytest.approx(0.05)

    def test_multiple_runs_ordered_newest_first(self, tmp_storage: PluginStorage) -> None:
        import time

        tmp_storage.save_run("run-001", 70, ["plugin-a"], 0.01)
        time.sleep(0.01)
        tmp_storage.save_run("run-002", 90, ["plugin-b"], 0.02)
        time.sleep(0.01)
        tmp_storage.save_run("run-003", 80, ["plugin-c"], 0.03)

        runs = tmp_storage.get_recent_runs(n=10)
        assert runs[0]["id"] == "run-003"
        assert runs[1]["id"] == "run-002"
        assert runs[2]["id"] == "run-001"

    def test_get_recent_runs_respects_limit(self, tmp_storage: PluginStorage) -> None:
        for i in range(5):
            tmp_storage.save_run(f"run-{i:03d}", 80, [], 0.0)
        runs = tmp_storage.get_recent_runs(n=3)
        assert len(runs) == 3

    def test_empty_plugins_run(self, tmp_storage: PluginStorage) -> None:
        tmp_storage.save_run("run-empty", 100, [], 0.0)
        runs = tmp_storage.get_recent_runs()
        assert runs[0]["plugins_run"] == []

    def test_get_recent_runs_empty_db(self, tmp_storage: PluginStorage) -> None:
        runs = tmp_storage.get_recent_runs()
        assert runs == []


class TestSavePluginResult:
    def test_save_plugin_result_persists(self, tmp_storage: PluginStorage) -> None:
        result = PluginResult(
            status="pass",
            findings=[{"key": "val"}],
            duration_ms=150.5,
            cost_usd=0.002,
        )
        tmp_storage.save_plugin_result("run-001", "unit-ai", result)

        cur = tmp_storage._conn.execute(
            "SELECT run_id, plugin_name, status, duration_ms, cost_usd FROM plugin_result"
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "run-001"
        assert row[1] == "unit-ai"
        assert row[2] == "pass"
        assert row[3] == pytest.approx(150.5)
        assert row[4] == pytest.approx(0.002)

    def test_findings_stored_as_json(self, tmp_storage: PluginStorage) -> None:
        findings = [{"file": "test.py", "issue": "dead assertion"}]
        result = PluginResult(status="warn", findings=findings)
        tmp_storage.save_plugin_result("run-x", "test-meta-quality", result)

        cur = tmp_storage._conn.execute("SELECT findings_json FROM plugin_result")
        row = cur.fetchone()
        stored_findings = json.loads(row[0])
        assert stored_findings == findings

    def test_save_multiple_results_for_same_run(self, tmp_storage: PluginStorage) -> None:
        for plugin_name in ["unit-ai", "security-scan", "e2e-playwright"]:
            result = PluginResult(status="pass")
            tmp_storage.save_plugin_result("run-multi", plugin_name, result)

        cur = tmp_storage._conn.execute(
            "SELECT COUNT(*) FROM plugin_result WHERE run_id='run-multi'"
        )
        count = cur.fetchone()[0]
        assert count == 3

    def test_save_error_result(self, tmp_storage: PluginStorage) -> None:
        result = PluginResult(status="error", findings=[{"error": "timeout"}])
        tmp_storage.save_plugin_result("run-err", "chaos-resilience", result)

        cur = tmp_storage._conn.execute("SELECT status FROM plugin_result WHERE run_id='run-err'")
        row = cur.fetchone()
        assert row[0] == "error"
