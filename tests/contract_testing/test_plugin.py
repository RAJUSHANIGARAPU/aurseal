"""Tests for contract_testing.pytest_plugin (ContractPlugin)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from contract_testing.config import ContractConfig
from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.pytest_plugin import ContractPlugin

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _config(tmp_path: Path, mode: str = "consumer") -> ContractConfig:
    return ContractConfig(
        enabled=True,
        mode=mode,
        contracts_dir=tmp_path / "contracts",
        consumer_name="wiki",
        provider_name="api",
    )


def _plugin(tmp_path: Path, mode: str = "consumer") -> ContractPlugin:
    return ContractPlugin(_config(tmp_path, mode=mode))


def _contract() -> Contract:
    return Contract(
        consumer="wiki",
        provider="api",
        interactions=[
            Interaction(
                description="GET /users",
                request=RequestSchema(method="GET", path="/users"),
                response=ResponseSchema(
                    status=200,
                    body_schema={
                        "type": "object",
                        "required": ["id"],
                        "properties": {"id": {"type": "integer"}},
                    },
                ),
            )
        ],
    )


def _raw_capture(method="GET", path="/users", status=200, body=None):
    return {
        "method": method,
        "path": path,
        "query": "",
        "request_headers": {},
        "request_body": None,
        "status": status,
        "response_headers": {"content-type": "application/json"},
        "response_body": body or {"id": 1},
        "test_name": "test_example",
    }


# ------------------------------------------------------------------
# from_config
# ------------------------------------------------------------------


def test_from_config_returns_plugin(tmp_path):
    plugin = ContractPlugin.from_config(_config(tmp_path))
    assert isinstance(plugin, ContractPlugin)


# ------------------------------------------------------------------
# pytest_sessionstart — consumer mode
# ------------------------------------------------------------------


def test_session_start_installs_capture(tmp_path):
    plugin = _plugin(tmp_path)
    with patch("contract_testing.capture.install") as mock_install:
        plugin.pytest_sessionstart(session=MagicMock())
        mock_install.assert_called_once()


def test_session_start_skips_capture_when_disabled(tmp_path):
    cfg = _config(tmp_path)
    cfg.mode = "observer"  # unsupported mode — should skip install
    plugin = ContractPlugin(cfg)
    with patch("contract_testing.capture.install") as mock_install:
        plugin.pytest_sessionstart(session=MagicMock())
        mock_install.assert_not_called()


# ------------------------------------------------------------------
# pytest_runtest_setup / teardown
# ------------------------------------------------------------------


def test_runtest_setup_activates_capture(tmp_path):
    plugin = _plugin(tmp_path)
    with patch("contract_testing.capture.install"):
        plugin.pytest_sessionstart(session=MagicMock())
    item = MagicMock()
    item.nodeid = "tests/test_foo.py::test_bar"
    with patch("contract_testing.capture.activate") as mock_activate:
        plugin.pytest_runtest_setup(item)
        mock_activate.assert_called_once_with(test_name=item.nodeid)


def test_runtest_teardown_collects_captures(tmp_path):
    plugin = _plugin(tmp_path)
    with patch("contract_testing.capture.install"):
        plugin.pytest_sessionstart(session=MagicMock())
    raw = _raw_capture()
    with patch("contract_testing.capture.deactivate", return_value=[raw]):
        plugin.pytest_runtest_teardown(item=MagicMock(), nextitem=None)
    assert len(plugin._raw_captures) == 1


# ------------------------------------------------------------------
# Provider validation
# ------------------------------------------------------------------


def test_provider_validation_runs_on_teardown(tmp_path):
    plugin = _plugin(tmp_path, mode="provider")
    plugin._validator_contract = _contract()
    raw = _raw_capture()
    with patch("contract_testing.capture.deactivate", return_value=[raw]):
        plugin.pytest_runtest_teardown(item=MagicMock(), nextitem=None)
    assert len(plugin._validation_results) >= 1


def test_provider_validation_skipped_when_no_contract(tmp_path):
    plugin = _plugin(tmp_path, mode="provider")
    plugin._validator_contract = None
    raw = _raw_capture()
    with patch("contract_testing.capture.deactivate", return_value=[raw]):
        plugin.pytest_runtest_teardown(item=MagicMock(), nextitem=None)
    assert plugin._validation_results == []


def test_provider_validation_filters_no_contract_pseudo_results(tmp_path):
    plugin = _plugin(tmp_path, mode="provider")
    plugin._validator_contract = _contract()
    raw = _raw_capture(path="/completely_unknown_endpoint_xyz")
    with patch("contract_testing.capture.deactivate", return_value=[raw]):
        plugin.pytest_runtest_teardown(item=MagicMock(), nextitem=None)
    # Unknown endpoint produces "(no contract for this endpoint)" — should be filtered
    assert all(r.errors != ["(no contract for this endpoint)"] for r in plugin._validation_results)


# ------------------------------------------------------------------
# pytest_sessionfinish
# ------------------------------------------------------------------


def test_session_finish_removes_capture(tmp_path):
    plugin = _plugin(tmp_path)
    with patch("contract_testing.capture.install"):
        plugin.pytest_sessionstart(session=MagicMock())
    with (
        patch("contract_testing.capture.remove") as mock_remove,
        patch.object(plugin, "_save_consumer_contract"),
    ):
        plugin.pytest_sessionfinish(session=MagicMock(), exitstatus=0)
        mock_remove.assert_called_once()


def test_session_finish_saves_contract_when_captures_exist(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._raw_captures = [_raw_capture()]
    with (
        patch("contract_testing.capture.remove"),
        patch.object(plugin, "_save_consumer_contract") as mock_save,
    ):
        plugin.pytest_sessionfinish(session=MagicMock(), exitstatus=0)
        mock_save.assert_called_once()


def test_session_finish_skips_save_when_no_captures(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._raw_captures = []
    with (
        patch("contract_testing.capture.remove"),
        patch.object(plugin, "_save_consumer_contract") as mock_save,
    ):
        plugin.pytest_sessionfinish(session=MagicMock(), exitstatus=0)
        mock_save.assert_not_called()
