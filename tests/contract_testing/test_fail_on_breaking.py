"""The contract gate can fail: a breaking change turns the session red (unless switched off)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.storage import ContractStore

pytest_plugins = ["pytester"]
ROOT = Path(__file__).resolve().parents[2]

CONFTEST = """
import os
from pathlib import Path

import requests

from contract_testing.config import ContractConfig
from contract_testing.pytest_plugin import ContractPlugin

PAYLOAD = {"payload": PAYLOAD_JSON}


def _fake_send(self, request, **kwargs):
    response = requests.Response()
    response.status_code = 200
    response.headers["content-type"] = "application/json"
    response._content = __import__("json").dumps(PAYLOAD["payload"]).encode()
    response.url = request.url
    response.request = request
    return response


def pytest_configure(config):
    requests.Session.send = _fake_send  # underneath the capture patch, never the network
    cfg = ContractConfig.from_env()
    cfg.enabled = True
    cfg.contracts_dir = Path("contracts")
    cfg.consumer_name, cfg.provider_name = "web", "api"
    config.pluginmanager.register(ContractPlugin.from_config(cfg), "contract-testing")
"""

TEST = """
import requests

def test_reads_users():
    assert requests.get("http://api.test/users").status_code == 200
"""


def _stored(directory: Path) -> ContractStore:
    store = ContractStore(directory)
    store.save(
        Contract(
            consumer="web",
            provider="api",
            interactions=[
                Interaction(
                    description="GET /users",
                    request=RequestSchema(method="GET", path="/users"),
                    response=ResponseSchema(
                        status=200,
                        body_schema={
                            "type": "object",
                            "required": ["id", "name"],
                            "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                        },
                    ),
                )
            ],
        )
    )
    return store


def _suite(pytester, monkeypatch, tmp_path, payload, **env):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    pytester.makeconftest(CONFTEST.replace("PAYLOAD_JSON", repr(payload)))
    pytester.makepyfile(test_users=TEST)
    store = _stored(pytester.path / "contracts")
    return store


def _verdicts(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_breaking_diff_fails_the_session_and_keeps_the_baseline(pytester, monkeypatch, tmp_path):
    store = _suite(pytester, monkeypatch, tmp_path, {"name": "x"})  # `id` is gone
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.TESTS_FAILED, result.stdout.str()
    result.stdout.fnmatch_lines(["*required field 'id' removed*", "*failing the run*"])
    assert store.list_versions("web", "api") == ["1.0.0"], "a breaking contract was saved"
    assert _verdicts(tmp_path)[-1]["kind"] == "contract"
    assert _verdicts(tmp_path)[-1]["verdict"] == "breaking"


def test_gate_off_reports_but_passes_and_accepts_the_change(pytester, monkeypatch, tmp_path):
    store = _suite(
        pytester, monkeypatch, tmp_path, {"name": "x"}, CONTRACT_FAIL_ON_BREAKING="false"
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
    result.stdout.fnmatch_lines(["*required field 'id' removed*"])
    assert store.list_versions("web", "api")[0] == "2.0.0"
    assert _verdicts(tmp_path)[-1]["verdict"] == "breaking"


def test_unchanged_contract_saves_no_new_version(pytester, monkeypatch, tmp_path):
    store = _suite(pytester, monkeypatch, tmp_path, {"id": 1, "name": "x"})
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
    assert store.list_versions("web", "api") == ["1.0.0"]
    assert _verdicts(tmp_path)[-1]["verdict"] == "compatible"


def test_provider_mismatch_fails_the_session(pytester, monkeypatch, tmp_path):
    _suite(
        pytester, monkeypatch, tmp_path, {"id": "not-an-int", "name": "x"}, CONTRACT_MODE="provider"
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.TESTS_FAILED, result.stdout.str()
    assert _verdicts(tmp_path)[-1]["verdict"] == "provider_mismatch"


def test_provider_mode_without_captures_is_no_evidence(pytester, monkeypatch, tmp_path):
    _suite(pytester, monkeypatch, tmp_path, {"id": 1}, CONTRACT_MODE="provider")
    pytester.makepyfile(test_users="def test_nothing():\n    pass\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.OK
    result.stdout.fnmatch_lines(["*no contract evidence*"])
    assert _verdicts(tmp_path)[-1]["verdict"] == "no_evidence"
