"""
OpenAPI / Swagger document -> a plan of API tests per tag, and the modules for it.

Per operation the plan has a success case and only the failure cases the operation
can produce:

* ``400`` when there is something required to leave out (a required parameter other
  than a path parameter, or a required request body);
* ``401`` when the operation is secured (its own ``security`` or the document's,
  unless the operation sets ``security: []``);
* ``404`` when the path names a resource (has a path parameter).

Every generated test carries the ``api`` marker and ``generated_batch(name=...)``, so
one batch is selected with ``-m "generated_batch(name='<feature>')"``. A tag without a
client in ``api/clients`` gets one; an existing client is reused, never duplicated.
Example bodies go to ``<out>/<feature>_api_data.json``.

    python -m tools.slash.openapi_plan summary <doc.json|yaml|url>
    python -m tools.slash.openapi_plan write <doc> --feature petstore [--out generated_tests]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from tools.slash.postman_plan import camel, ident

HTTP_METHODS = ("get", "put", "post", "delete", "patch")
CLIENT_DIR = Path("api/clients")


@dataclass
class Case:
    kind: str  # success | missing_required | unauthorised | not_found
    status: int


@dataclass
class Operation:
    method: str
    path: str
    tag: str
    op_id: str
    cases: list[Case] = field(default_factory=list)
    example: object = None


def load(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=15) as response:  # noqa: S310 - user-given URL
            text = response.read(10 * 1024 * 1024).decode("utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError:
        import yaml

        return yaml.safe_load(text)


def _resolve(doc: dict, node):
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        target = doc
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node, seen = target, seen + 1
    return node


def example_for(doc: dict, schema, depth: int = 0):
    schema = _resolve(doc, schema) or {}
    if "example" in schema:
        return schema["example"]
    if depth > 6:
        return None
    kind = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        return {name: example_for(doc, sub, depth + 1) for name, sub in props.items()}
    if kind == "array":
        return [example_for(doc, schema.get("items") or {}, depth + 1)]
    return {"integer": 1, "number": 1.5, "boolean": True}.get(kind, "example")


def _success(responses: dict) -> int:
    codes = sorted(int(c) for c in responses if str(c).isdigit() and 200 <= int(c) < 300)
    return codes[0] if codes else 200


def plan(doc: dict) -> list[Operation]:
    global_security = doc.get("security") or []
    ops: list[Operation] = []
    for path, item in (doc.get("paths") or {}).items():
        shared = item.get("parameters") or []
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = [_resolve(doc, p) for p in shared + (op.get("parameters") or [])]
            body = _resolve(doc, op.get("requestBody") or {})
            body_params = [p for p in params if p.get("in") == "body"]  # Swagger 2
            schema = None
            if body:
                content = body.get("content") or {}
                schema = (content.get("application/json") or next(iter(content.values()), {})).get(
                    "schema"
                )
            elif body_params:
                schema = body_params[0].get("schema")
            tag = (op.get("tags") or ["default"])[0]
            op_id = op.get("operationId") or f"{method}_{path}"
            entry = Operation(method.upper(), path, tag, ident(op_id))
            entry.cases.append(Case("success", _success(op.get("responses") or {})))
            required = any(p.get("required") and p.get("in") != "path" for p in params)
            if required or body.get("required") or any(p.get("required") for p in body_params):
                entry.cases.append(Case("missing_required", 400))
            security = op["security"] if "security" in op else global_security
            if security:
                entry.cases.append(Case("unauthorised", 401))
            if "{" in path:
                entry.cases.append(Case("not_found", 404))
            if schema is not None:
                entry.example = example_for(doc, schema)
            ops.append(entry)
    return ops


def by_tag(ops: list[Operation]) -> dict[str, list[Operation]]:
    groups: dict[str, list[Operation]] = {}
    for op in ops:
        groups.setdefault(op.tag, []).append(op)
    return groups


def summary(doc: dict, preview: int = 5) -> str:
    info = doc.get("info") or {}
    paths = list((doc.get("paths") or {}).keys())
    lines = [f"Title: {info.get('title', '?')}", f"Paths: {len(paths)}"]
    lines += [f"- {p}" for p in paths[:preview]]
    schemes = (doc.get("components") or {}).get("securitySchemes") or doc.get("securityDefinitions")
    lines.append(f"Security schemes: {', '.join(schemes) if schemes else 'none'}")
    return "\n".join(lines)


def existing_client(tag: str, client_dir: Path = CLIENT_DIR) -> str | None:
    path = client_dir / f"{ident(tag)}_client.py"
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    return f"api.clients.{path.stem}" if f"{camel(tag)}Client" in names else None


def render_client(tag: str, ops: list[Operation]) -> str:
    out = [
        f'"""Client for the "{tag}" operations (generated from an OpenAPI document)."""',
        "",
        "import requests",
        "",
        "",
        f"class {camel(tag)}Client:",
        "    def __init__(self, base_url, token=None, timeout=10):",
        '        self.base_url = base_url.rstrip("/")',
        "        self.token = token",
        "        self.timeout = timeout",
        "",
        "    def call(self, method, path, json=None, params=None, auth=True):",
        "        headers = {}",
        "        if auth and self.token:",
        '            headers["Authorization"] = f"Bearer {self.token}"',
        "        return requests.request(",
        "            method,",
        "            self.base_url + path,",
        "            json=json,",
        "            params=params,",
        "            headers=headers,",
        "            timeout=self.timeout,",
        "        )",
    ]
    return "\n".join(out) + "\n"


def _path_args(path: str, missing: bool) -> str:
    value = "does-not-exist-0" if missing else "1"
    return repr(re.sub(r"\{[^}]+\}", value, path))


def render_module(feature: str, tag: str, ops: list[Operation], client_module: str) -> str:
    cls = f"{camel(tag)}Client"
    out = [
        f'"""API tests for the "{tag}" operations (generated from an OpenAPI document)."""',
        "",
        "import json",
        "from pathlib import Path",
        "",
        "import pytest",
        "",
        f"from {client_module} import {cls}",
        "",
        f'pytestmark = [pytest.mark.api, pytest.mark.generated_batch(name="{feature}")]',
        "",
        f'DATA = json.loads((Path(__file__).parent / "{feature}_api_data.json").read_text())',
        "",
        "",
        "@pytest.fixture",
        "def client(config):",
        f'    return {cls}(config.get_base_url(), token=config.get_str("api_token", None))',
    ]
    for op in ops:
        body = f'DATA.get("{op.op_id}")'
        for case in op.cases:
            name = f"test_{op.op_id}_{case.kind}"
            out += ["", "", f"def {name}(client):"]
            out.append(f'    """{op.method} {op.path}: expect {case.status}."""')
            path = _path_args(op.path, case.kind == "not_found")
            if case.kind == "missing_required":
                call = f'client.call("{op.method}", {path}, json={{}})'
            elif case.kind == "unauthorised":
                call = f'client.call("{op.method}", {path}, json={body}, auth=False)'
            else:
                call = f'client.call("{op.method}", {path}, json={body})'
            out.append(f"    response = {call}")
            out.append(f"    assert response.status_code == {case.status}, response.text[:300]")
            if case.kind == "success" and op.example is not None and op.method != "DELETE":
                out.append("    if response.content:")
                out.append("        assert isinstance(response.json(), (dict, list))")
    return "\n".join(out) + "\n"


def write(doc: dict, feature: str, out_dir: Path, client_dir: Path = CLIENT_DIR) -> list[Path]:
    feature = ident(feature)
    target = out_dir / feature
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").touch()
    ops = plan(doc)
    written = []
    data = {op.op_id: op.example for op in ops if op.example is not None}
    data_path = target / f"{feature}_api_data.json"
    data_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    written.append(data_path)
    for tag, tag_ops in by_tag(ops).items():
        module = existing_client(tag, client_dir)
        if module is None:
            client_path = target / f"{ident(tag)}_client.py"
            client_path.write_text(render_client(tag, tag_ops), encoding="utf-8")
            written.append(client_path)
            module = f".{client_path.stem}"
        test_path = target / f"test_{feature}_{ident(tag)}_api.py"
        test_path.write_text(render_module(feature, tag, tag_ops, module), encoding="utf-8")
        written.append(test_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI document -> API test plan and modules.")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("summary")
    s.add_argument("source")
    w = sub.add_parser("write")
    w.add_argument("source")
    w.add_argument("--feature", required=True)
    w.add_argument("--out", type=Path, default=Path("generated_tests"))
    args = parser.parse_args(argv)
    doc = load(args.source)
    if args.command == "summary":
        print(summary(doc))
        return 0
    for path in write(doc, args.feature, args.out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
