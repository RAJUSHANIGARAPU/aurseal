"""The gate's answers and the CLI exit codes; the demo service's spec is gated too."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

from contract_testing.gate import check_contracts, check_openapi, main, record
from contract_testing.models import Contract, Interaction, RequestSchema, ResponseSchema
from contract_testing.storage import ContractStore
from tests.contract_testing.test_openapi_diff import BASE

ROOT = Path(__file__).resolve().parents[2]


def _write(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _broken():
    new = copy.deepcopy(BASE)
    new["paths"].pop("/health")
    return new


def test_openapi_gate_verdicts():
    assert check_openapi(BASE, copy.deepcopy(BASE)).verdict == "compatible"
    broken = check_openapi(BASE, _broken())
    assert broken.verdict == "breaking" and broken.exit_code == 1
    assert check_openapi(BASE, {"nope": 1}).verdict == "no_evidence"


def test_cli_openapi_exit_codes_and_records(tmp_path):
    old = _write(tmp_path, "old.json", BASE)
    same = _write(tmp_path, "same.json", BASE)
    new = _write(tmp_path, "new.json", _broken())
    out = io.StringIO()
    assert main(["openapi", str(old), str(same)], out) == 0
    assert main(["openapi", str(old), str(new)], out) == 1
    assert "path removed" in out.getvalue()
    assert main(["openapi", str(old), str(tmp_path / "missing.json")], out) == 2
    rows = (tmp_path / "metrics" / "metric_contract_change.jsonl").read_text().splitlines()
    assert any(json.loads(r)["change_type"] == "breaking" for r in rows)
    verdicts = [
        json.loads(v)["verdict"] for v in (tmp_path / "verdicts.jsonl").read_text().splitlines()
    ]
    assert verdicts == ["compatible", "breaking", "no_evidence"]


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
                    body_schema={
                        "type": "object",
                        "required": required,
                        "properties": {k: {"type": "string"} for k in required},
                    },
                ),
            )
        ],
    )


def test_store_gate(tmp_path):
    store = ContractStore(tmp_path / "contracts")
    assert check_contracts(store, "web", "api").verdict == "no_evidence"
    store.save(_contract("1.0.0", ["id", "name"]))
    assert check_contracts(store, "web", "api").verdict == "compatible"
    store.save(_contract("2.0.0", ["id"]))
    result = check_contracts(store, "web", "api")
    assert result.verdict == "breaking"
    assert result.breaking[0].location == "GET /users"
    assert (result.from_version, result.to_version) == ("1.0.0", "2.0.0")


def test_cli_store_missing_dir_is_no_evidence(tmp_path):
    out = io.StringIO()
    args = ["store", "--consumer", "web", "--provider", "api", "--dir", str(tmp_path / "x")]
    assert main(args, out) == 2
    assert "NO EVIDENCE" in out.getvalue()


def test_record_without_changes_writes_only_the_verdict(tmp_path):
    assert record(check_openapi(BASE, BASE), consumer="c", provider="p", source="openapi") is None
    assert not (tmp_path / "metrics").exists()


def test_demo_service_spec_has_no_unreviewed_break():
    """The committed demo spec against the one the demo app generates now."""
    from demo_sut.openapi import build

    committed = json.loads((ROOT / "demo_sut" / "openapi.json").read_text(encoding="utf-8"))
    result = check_openapi(committed, build())
    assert result.evidence
    assert result.breaking == [], result.describe()
