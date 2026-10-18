"""One-way export of a stored contract to a Pact v3 JSON file.

For a team that already runs Pact tooling: the stored contract's request and
response body schemas become example bodies plus ``type`` matching rules, so a
Pact verifier checks types rather than literal values.

This is a Pact-compatible *shape*, not full Pact provider verification: there are
no provider-state handlers, and JSON-Schema details with no Pact matcher (formats,
numeric ranges, ``oneOf``) are lost. No broker client.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from contract_testing.models import Contract, Interaction

PACT_VERSION = "3.0.0"
_EXAMPLES = {"string": "string", "integer": 1, "number": 1.5, "boolean": True, "null": None}
_SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def example_from_schema(schema: dict) -> Any:
    """A value of the schema's shape (first enum value when there is an enum)."""
    if not schema:
        return None
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or (kind is None and "properties" in schema):
        return {k: example_from_schema(v) for k, v in (schema.get("properties") or {}).items()}
    if kind == "array":
        return [example_from_schema(schema.get("items") or {})]
    return _EXAMPLES.get(kind)


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if _SAFE_KEY.match(key) else f"{path}['{key}']"


def matching_rules(schema: dict, path: str = "$") -> dict[str, dict]:
    """Pact v3 body matching rules: ``type`` on every node, ``min: 0`` on arrays."""
    rules: dict[str, dict] = {}
    if not schema:
        return rules
    kind = schema.get("type")
    if kind == "array":
        rules[path] = {"matchers": [{"match": "type", "min": 0}]}
        rules.update(matching_rules(schema.get("items") or {}, f"{path}[*]"))
        return rules
    if kind == "object" or "properties" in schema:
        for key, sub in (schema.get("properties") or {}).items():
            rules.update(matching_rules(sub, _child(path, key)))
        return rules
    if path != "$":
        rules[path] = {"matchers": [{"match": "type"}]}
    return rules


def _query(query: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pair in filter(None, query.split("&")):
        key, _, value = pair.partition("=")
        out.setdefault(key, []).append(value)
    return out


def _interaction(interaction: Interaction) -> dict:
    request: dict[str, Any] = {
        "method": interaction.request.method.upper(),
        "path": interaction.request.path,
    }
    if interaction.request.query:
        request["query"] = _query(interaction.request.query)
    if interaction.request.headers:
        request["headers"] = interaction.request.headers
    if interaction.request.body_schema:
        request["body"] = example_from_schema(interaction.request.body_schema)
        rules = matching_rules(interaction.request.body_schema)
        if rules:
            request["matchingRules"] = {"body": rules}

    response: dict[str, Any] = {"status": interaction.response.status}
    if interaction.response.headers:
        response["headers"] = interaction.response.headers
    if interaction.response.body_schema:
        response["body"] = example_from_schema(interaction.response.body_schema)
        rules = matching_rules(interaction.response.body_schema)
        if rules:
            response["matchingRules"] = {"body": rules}

    return {
        "description": interaction.description or interaction.key,
        "providerStates": [{"name": state} for state in interaction.provider_states],
        "request": request,
        "response": response,
    }


def to_pact_v3(contract: Contract) -> dict:
    return {
        "consumer": {"name": contract.consumer},
        "provider": {"name": contract.provider},
        "interactions": [_interaction(i) for i in contract.interactions],
        "metadata": {
            "pactSpecification": {"version": PACT_VERSION},
            "contractVersion": contract.version,
        },
    }


def write_pact(contract: Contract, out_dir: str | Path) -> Path:
    """Write ``<consumer>-<provider>.json`` into ``out_dir``; returns the path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{contract.consumer}-{contract.provider}.json"
    path.write_text(json.dumps(to_pact_v3(contract), indent=2), encoding="utf-8")
    return path


def main(argv=None) -> int:  # pragma: no cover - thin CLI
    import argparse

    from contract_testing.storage import ContractStore

    parser = argparse.ArgumentParser(prog="python -m contract_testing.pact_export")
    parser.add_argument("--dir", default="contracts")
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--out", default="reports/pacts")
    args = parser.parse_args(argv)
    contract = ContractStore(Path(args.dir)).load_latest(args.consumer, args.provider)
    if contract is None:
        print(f"no stored contract for {args.consumer}/{args.provider}")
        return 2
    print(write_pact(contract, args.out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
