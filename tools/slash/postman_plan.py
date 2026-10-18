"""
Fallback when the API generation pipeline cannot turn a Postman collection into tests.

One test module per top-level folder (requests outside any folder go to a module named
after the collection), one test function per request, one client class per folder
unless ``api/clients`` already has a client with every method the module needs.

What the collection's test scripts check is translated into pytest assertions with the
same meaning:

=================================================  ==========================================
``pm.response.to.have.status(201)``                ``assert response.status_code == 201``
``pm.response.to.be.json``                         JSON content type
``pm.expect(json.a.b).to.eql(3)``                  ``assert body["a"]["b"] == 3``
``pm.expect(json.items).to.have.lengthOf(2)``      ``assert len(body["items"]) == 2``
``pm.response.to.have.header("X-Id")``             ``assert "X-Id" in response.headers``
=================================================  ==========================================

Anything else is counted and left as a review note, never guessed. Credentials are never
copied: an ``auth`` block, a secret-looking header, query parameter or variable becomes a
lookup in the environment configuration instead of a literal.

    python -m tools.slash.postman_plan summary <collection.json>
    python -m tools.slash.postman_plan write <collection.json> [--out generated_tests]
"""

from __future__ import annotations

import argparse
import ast
import json
import keyword
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

SECRET_KEY = re.compile(
    r"token|secret|password|passwd|api[-_]?key|auth|cookie|session|credential", re.I
)
SECRET_VALUE = re.compile(r"^(?:bearer|basic|token)\s+\S+|^[A-Za-z0-9_\-.+/=]{24,}$", re.I)
_VAR = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
CLIENT_DIR = Path("api/clients")


def ident(text: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"
    if name[0].isdigit() or keyword.iskeyword(name):
        name = f"r_{name}"
    return name


def camel(text: str) -> str:
    return "".join(part.capitalize() for part in ident(text).split("_"))


@dataclass
class Request:
    name: str
    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: object = None
    checks: list[str] = field(default_factory=list)
    untranslated: int = 0
    secret_inputs: list[str] = field(default_factory=list)  # config keys looked up at run time


@dataclass
class Group:
    name: str
    requests: list[Request] = field(default_factory=list)

    @property
    def module(self) -> str:
        return f"test_{ident(self.name)}_api.py"

    @property
    def client_class(self) -> str:
        return f"{camel(self.name)}Client"


def _value(raw: str, key: str, req: Request) -> str:
    """A literal, or a config placeholder when the key or value is secret-like."""
    secret_var = any(SECRET_KEY.search(v) for v in _VAR.findall(raw))
    if SECRET_KEY.search(key) or secret_var or SECRET_VALUE.search(raw.strip()):
        config_key = ident(key)
        req.secret_inputs.append(config_key)
        return "{{config:" + config_key + "}}"
    return raw


def _path(url) -> tuple[str, dict[str, str]]:
    if isinstance(url, dict):
        path = "/" + "/".join(str(p) for p in url.get("path") or [])
        query = {
            q.get("key", ""): str(q.get("value", ""))
            for q in url.get("query") or []
            if not q.get("disabled")
        }
        return path, query
    raw = _VAR.sub("", str(url))
    parts = urlsplit(raw if "://" in raw else "http://host/" + raw.lstrip("/"))
    query = dict(p.split("=", 1) if "=" in p else (p, "") for p in parts.query.split("&") if p)
    return parts.path or "/", query


def _accessor(expr: str) -> str | None:
    """``jsonData.a.b[0]`` -> ``body["a"]["b"][0]``."""
    m = re.fullmatch(
        r"(?:jsonData|json|data|body|pm\.response\.json\(\))((?:\.\w+|\[\d+\])*)", expr.strip()
    )
    if not m:
        return None
    out = "body"
    for part in re.findall(r"\.(\w+)|\[(\d+)\]", m.group(1)):
        out += f'["{part[0]}"]' if part[0] else f"[{part[1]}]"
    return out


def translate(line: str) -> list[str]:
    """Assertions for one script line; [] when nothing in it is understood."""
    out: list[str] = []
    for code in re.findall(
        r"to\.have\.status\((\d{3})\)|response\.code\)\.to\.(?:eql|equal)\((\d{3})\)", line
    ):
        out.append(f"assert response.status_code == {code[0] or code[1]}")
    if re.search(
        r"to\.be\.json\b|content-type['\"]\)\)?\.to\.include\(['\"]application/json", line, re.I
    ):
        out.append('assert "application/json" in response.headers.get("Content-Type", "")')
    for name in re.findall(r"to\.have\.header\(['\"]([^'\"]+)['\"]", line):
        out.append(f'assert "{name}" in response.headers')
    for name in re.findall(r"headers\.has\(['\"]([^'\"]+)['\"]\)\)\.to\.be\.true", line):
        out.append(f'assert "{name}" in response.headers')
    for expr, n in re.findall(
        r"pm\.expect\(([\w.\[\]()]+?)(?:\.length)?\)\.to\.have\.lengthOf\((\d+)\)", line
    ):
        if (acc := _accessor(expr)) is not None:
            out.append(f"assert len({acc}) == {n}")
    for expr, n in re.findall(
        r"pm\.expect\(([\w.\[\]()]+?)\.length\)\.to\.(?:eql|equal)\((\d+)\)", line
    ):
        if (acc := _accessor(expr)) is not None:
            out.append(f"assert len({acc}) == {n}")
    for expr, raw in re.findall(
        r"pm\.expect\(([\w.\[\]()]+)\)\.to\.(?:eql|equal)\(([^()]+)\)", line
    ):
        if expr.endswith(".length") or "response.code" in expr:
            continue
        acc = _accessor(expr)
        try:
            value = ast.literal_eval(
                raw.strip()
                .replace("true", "True")
                .replace("false", "False")
                .replace("null", "None")
            )
        except (ValueError, SyntaxError):
            continue
        if acc is not None:
            out.append(f"assert {acc} == {value!r}")
    return out


def parse(collection: dict) -> tuple[str, list[Group]]:
    title = (collection.get("info") or {}).get("name") or "collection"
    groups: dict[str, Group] = {}

    def add(item, group_name):
        request = item.get("request") or {}
        if isinstance(request, str):
            request = {"url": request}
        path, query = _path(request.get("url") or "/")
        req = Request(item.get("name") or path, str(request.get("method") or "GET").upper(), path)
        for key, value in query.items():
            req.query[key] = _value(value, key, req)
        for header in request.get("header") or []:
            if not header.get("disabled"):
                key = header.get("key", "")
                req.headers[key] = _value(str(header.get("value", "")), key, req)
        if request.get("auth"):
            req.secret_inputs.append("api_token")
            req.headers["Authorization"] = "{{config:api_token}}"
        body = request.get("body") or {}
        if body.get("mode") == "raw" and body.get("raw"):
            try:
                req.body = json.loads(_VAR.sub("0", body["raw"]))
            except ValueError:
                req.body = None
            req.body = _strip_secrets(req.body, req)
        for event in item.get("event") or []:
            if event.get("listen") != "test":
                continue
            lines = (event.get("script") or {}).get("exec") or []
            for line in lines if isinstance(lines, list) else [lines]:
                found = translate(line)
                req.checks += found
                if not found and "pm." in line:
                    req.untranslated += 1
        groups.setdefault(group_name, Group(group_name)).requests.append(req)

    def walk(items, group_name):
        for item in items or []:
            if "item" in item:
                walk(item["item"], group_name or item.get("name", "folder"))
            else:
                add(item, group_name or title)

    walk(collection.get("item"), None)
    return title, list(groups.values())


def _strip_secrets(value, req: Request):
    if isinstance(value, dict):
        out = {}
        for key, inner in value.items():
            if SECRET_KEY.search(str(key)) or (
                isinstance(inner, str) and SECRET_VALUE.search(inner)
            ):
                req.secret_inputs.append(ident(str(key)))
                out[key] = "{{config:" + ident(str(key)) + "}}"
            else:
                out[key] = _strip_secrets(inner, req)
        return out
    if isinstance(value, list):
        return [_strip_secrets(v, req) for v in value]
    return value


def summary(collection: dict, preview: int = 5) -> str:
    title, groups = parse(collection)
    lines = [f"Collection: {title}", f"Top-level items: {len(collection.get('item') or [])}"]
    flat = [r for g in groups for r in g.requests]
    for req in flat[:preview]:
        lines.append(f"- {req.name}: {req.method} {req.path}")
    return "\n".join(lines)


def existing_client(group: Group, client_dir: Path = CLIENT_DIR) -> str | None:
    """Module path of an existing client that has every method this group needs."""
    path = client_dir / f"{ident(group.name)}_client.py"
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == group.client_class:
            methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
            if {ident(r.name) for r in group.requests} <= methods:
                return f"api.clients.{path.stem}"
    return None


def _render_literal(value) -> str:
    """A Python expression; config placeholders become config lookups."""
    if isinstance(value, str):
        m = re.fullmatch(r"\{\{config:(\w+)\}\}", value)
        if m:
            return f'config.get_str("{m.group(1)}", "")'
        if _VAR.search(value):
            return (
                repr(_VAR.sub(lambda v: "{" + ident(v.group(1)) + "}", value))
                + ".format(**variables)"
            )
        return repr(value)
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k!r}: {_render_literal(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_render_literal(v) for v in value) + "]"
    return repr(value)


def render_client(group: Group) -> str:
    out = [
        f'"""Client for the "{group.name}" requests (generated from a Postman collection)."""',
        "",
        "import requests",
        "",
        "",
        f"class {group.client_class}:",
        "    def __init__(self, base_url, session=None, timeout=10):",
        '        self.base_url = base_url.rstrip("/")',
        "        self.session = session or requests.Session()",
        "        self.timeout = timeout",
    ]
    for req in group.requests:
        out += [
            "",
            f"    def {ident(req.name)}(self, params=None, headers=None, json=None):",
            f'        """{req.method} {req.path}"""',
            "        return self.session.request(",
            f'            "{req.method}",',
            f'            self.base_url + "{req.path}",',
            "            params=params,",
            "            headers=headers,",
            "            json=json,",
            "            timeout=self.timeout,",
            "        )",
        ]
    return "\n".join(out) + "\n"


def _variables(group: Group) -> list[str]:
    names: list[str] = []

    def scan(value):
        if isinstance(value, str):
            names.extend(ident(v) for v in _VAR.findall(value))
        elif isinstance(value, dict):
            for inner in value.values():
                scan(inner)
        elif isinstance(value, list):
            for inner in value:
                scan(inner)

    for req in group.requests:
        scan(req.query)
        scan(req.headers)
        scan(req.body)
    return list(dict.fromkeys(names))


def render_module(
    title: str, group: Group, client_module: str, defaults: dict[str, str] | None = None
) -> str:
    defaults = defaults or {}
    out = [
        f'"""API tests for "{group.name}", generated from the Postman collection "{title}".',
        "",
        "Generated for review: check every assertion against the API's contract before",
        "moving this module out of the generated folder.",
        '"""',
        "",
        "import pytest",
        "",
        f"from {client_module} import {group.client_class}",
        "",
        "pytestmark = pytest.mark.api",
        "",
        "",
        "@pytest.fixture",
        "def client(config):",
        f"    return {group.client_class}(config.get_base_url())",
        "",
        "",
        "@pytest.fixture",
        "def variables(config):",
        '    """Collection variables, read from the environment configuration."""',
        "    return {",
    ]
    for name in _variables(group):
        default = "" if SECRET_KEY.search(name) else defaults.get(name, "")
        out.append(f"        {name!r}: config.get_str({name!r}, {default!r}),")
    out.append("    }")
    for req in group.requests:
        out += ["", "", f"def test_{ident(req.name)}(client, config, variables):"]
        out.append(f'    """{req.name}: {req.method} {req.path}"""')
        args = []
        if req.query:
            args.append(f"params={_render_literal(req.query)}")
        if req.headers:
            args.append(f"headers={_render_literal(req.headers)}")
        if req.body is not None:
            args.append(f"json={_render_literal(req.body)}")
        out.append(f"    response = client.{ident(req.name)}({', '.join(args)})")
        checks = req.checks or ["assert response.status_code < 400"]
        if any("body" in c for c in checks):
            out.append("    body = response.json()")
        out += [f"    {c}, response.text[:300]" for c in checks]
        if req.untranslated:
            out.append(f"    # review: {req.untranslated} script check(s) were not translated")
    return "\n".join(out) + "\n"


def write(collection_path: Path, out_dir: Path, client_dir: Path = CLIENT_DIR) -> list[Path]:
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    title, groups = parse(collection)
    defaults = {
        ident(str(v.get("key", ""))): str(v.get("value", ""))
        for v in collection.get("variable") or []
        if isinstance(v, dict)
        and v.get("type") != "secret"
        and not SECRET_VALUE.search(str(v.get("value", "")))
    }
    target = out_dir / ident(title)
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").touch()
    written = []
    for group in groups:
        module = existing_client(group, client_dir)
        if module is None:
            client_path = target / f"{ident(group.name)}_client.py"
            client_path.write_text(render_client(group), encoding="utf-8")
            written.append(client_path)
            module = f".{client_path.stem}"
        test_path = target / group.module
        test_path.write_text(render_module(title, group, module, defaults), encoding="utf-8")
        written.append(test_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Postman collection -> pytest API tests (fallback)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("summary")
    s.add_argument("collection", type=Path)
    w = sub.add_parser("write")
    w.add_argument("collection", type=Path)
    w.add_argument("--out", type=Path, default=Path("generated_tests"))
    args = parser.parse_args(argv)
    if args.command == "summary":
        print(summary(json.loads(args.collection.read_text(encoding="utf-8"))))
        return 0
    for path in write(args.collection, args.out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
