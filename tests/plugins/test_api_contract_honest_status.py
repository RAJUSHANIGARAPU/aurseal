"""api-contract runs the real gate: breaking is fail, compatible is pass, no inputs is unknown."""

from __future__ import annotations

import copy
import json

import pytest

from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.storage import ContractStore
from tests.contract_testing.test_openapi_diff import BASE
from tests.plugins._tier1 import load


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))


@pytest.fixture
def plugin():
    return load("api_contract").ApiContractPlugin()


def _broken():
    new = copy.deepcopy(BASE)
    new["paths"].pop("/health")
    return new


def test_breaking_spec_change_is_a_fail(plugin, tmp_path):
    result = plugin.run({"old_spec": BASE, "new_spec": _broken()})
    assert result.status == "fail"
    assert any("path removed" in b for b in result.findings[0]["breaking"])
    verdict = json.loads((tmp_path / "verdicts.jsonl").read_text().splitlines()[-1])
    assert (verdict["kind"], verdict["verdict"]) == ("contract", "breaking")


def test_compatible_spec_is_a_pass(plugin):
    assert plugin.run({"old_spec": BASE, "new_spec": copy.deepcopy(BASE)}).status == "pass"


def test_spec_files_are_read_from_disk(plugin, tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(BASE)), new.write_text(json.dumps(_broken()))
    assert plugin.run({"old_spec": str(old), "new_spec": str(new)}).status == "fail"


def test_unreadable_spec_is_unknown(plugin, tmp_path):
    result = plugin.run({"old_spec": BASE, "new_spec": str(tmp_path / "missing.json")})
    assert result.status == "unknown"


def test_no_inputs_is_unknown_not_the_library_tests(plugin):
    """It used to run the contract library's own unit tests and call that a pass."""
    result = plugin.run({})
    assert result.status == "unknown"
    assert "nothing was checked" in result.findings[0]["reason"]


def _contract(version, required):
    return Contract(
        consumer="web",
        provider="api",
        version=version,
        interactions=[
            Interaction(
                description="GET /users",
                request=RequestSchema(method="GET", path="/users"),
                response=ResponseSchema(
                    status=200,
                    body_schema={"type": "object", "required": required, "properties": {}},
                ),
            )
        ],
    )


def test_stored_contract_pair(plugin, tmp_path):
    directory = tmp_path / "contracts"
    ctx = {"consumer": "web", "provider": "api", "contracts_dir": str(directory)}
    assert plugin.run(ctx).status == "unknown"  # directory missing
    store = ContractStore(directory)
    assert plugin.run(ctx).status == "unknown"  # nothing stored
    store.save(_contract("1.0.0", ["id", "name"]))
    assert plugin.run(ctx).status == "pass"
    store.save(_contract("2.0.0", ["id"]))
    assert plugin.run(ctx).status == "fail"


def test_dry_run_checks_nothing(plugin, tmp_path):
    result = plugin.dry_run({"old_spec": BASE, "new_spec": _broken()})
    assert result.status == "skip" and result.dry_run
    assert not (tmp_path / "verdicts.jsonl").exists()
