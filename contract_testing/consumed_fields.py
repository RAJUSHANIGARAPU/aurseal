"""Check what consumers read against the deployed service's published API document.

The diff gates compare two contract versions; that needs an older document,
and a field nobody consumes can break without anyone noticing. This check
needs neither: it reads the API document the deployed service publishes and
requires every field and route its consumers declare they read.

The declaration is a small YAML file::

    schemas:
      Subscription: [id, number, status]
      Invoice: [id, total]
    routes:
      - GET /subscriptions/{id}
      - [POST, "/subscriptions/{id}/drafts"]   # quoted: braces end a YAML flow list

:func:`missing` returns what the document lacks, as ``Schema.field`` and
``METHOD path``. Paths match when they are equal after a servlet or gateway
prefix (``/api``, ``/service/v1``) is ignored on either side, and path
parameters match whatever they are named. Schema properties are read through
local ``$ref`` and ``allOf``.

CLI: ``python -m contract_testing.consumed_fields DOC.json consumed.yaml``
(exit 1 when something is missing).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
_PARAM = re.compile(r"\{[^}/]+\}")


def _resolve(document: Mapping[str, Any], node: Any) -> Any:
    hops = 0
    while isinstance(node, Mapping) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported, got {ref!r}")
        target: Any = document
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        node = target
        hops += 1
        if hops > 32:
            raise ValueError(f"$ref cycle at {ref}")
    return node


def schema_properties(document: Mapping[str, Any], schema: Any) -> set[str]:
    """Property names a schema declares, through ``$ref`` and ``allOf``."""
    node = _resolve(document, schema)
    if not isinstance(node, Mapping):
        return set()
    names = set(node.get("properties") or {})
    for part in node.get("allOf") or []:
        names |= schema_properties(document, part)
    return names


def _schemas(document: Mapping[str, Any]) -> Mapping[str, Any]:
    components = document.get("components") or {}
    return (components.get("schemas") if isinstance(components, Mapping) else None) or (
        document.get("definitions") or {}
    )


def _segments(path: str) -> list[str]:
    return [_PARAM.sub("{}", s) for s in path.strip().strip("/").split("/") if s]


def path_matches(consumer: str, served: str) -> bool:
    """Equal, or equal once a leading prefix is dropped from the longer side."""
    a, b = _segments(consumer), _segments(served)
    if len(a) > len(b):
        a, b = b, a
    if not a:
        return not b
    return b[len(b) - len(a) :] == a


def missing(
    document: Mapping[str, Any],
    consumed: Mapping[str, Iterable[str]],
    routes: Iterable[tuple[str, str]],
) -> list[str]:
    """``Schema.field`` and ``METHOD path`` entries the document does not provide.

    ``routes`` holds ``(path, method)`` pairs.
    """
    out: list[str] = []
    schemas = _schemas(document)
    for schema, fields in consumed.items():
        declared = schema_properties(document, schemas[schema]) if schema in schemas else set()
        out += [f"{schema}.{name}" for name in fields if name not in declared]
    served = [
        (path, method)
        for path, ops in (document.get("paths") or {}).items()
        if isinstance(ops, Mapping)
        for method in ops
        if method.lower() in _METHODS
    ]
    for path, method in routes:
        verb = method.lower()
        if not any(m.lower() == verb and path_matches(path, p) for p, m in served):
            out.append(f"{method.upper()} {path}")
    return out


def _route(raw: Any) -> tuple[str, str]:
    if isinstance(raw, str):
        method, _, path = raw.strip().partition(" ")
    elif isinstance(raw, Sequence) and len(raw) == 2:
        method, path = str(raw[0]), str(raw[1])
    else:
        raise ValueError(f"route {raw!r} is neither 'METHOD /path' nor [METHOD, /path]")
    if method.lower() not in _METHODS or not path.strip().startswith("/"):
        raise ValueError(f"route {raw!r} is neither 'METHOD /path' nor [METHOD, /path]")
    return path.strip(), method.upper()


def load_declaration(path: str | Path) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """``(consumed, routes)`` from a declaration YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: expected a mapping with 'schemas' and/or 'routes'")
    consumed = {str(k): [str(f) for f in v or []] for k, v in (raw.get("schemas") or {}).items()}
    routes = [_route(r) for r in raw.get("routes") or []]
    if not consumed and not routes:
        raise ValueError(f"{path}: declares no schemas and no routes; nothing would be checked")
    return consumed, routes


def main(argv: Sequence[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m contract_testing.consumed_fields")
    parser.add_argument("document", help="the published API document (JSON)")
    parser.add_argument("declaration", help="YAML: schemas -> fields, routes")
    args = parser.parse_args(argv)
    document = json.loads(Path(args.document).read_text(encoding="utf-8"))
    consumed, routes = load_declaration(args.declaration)
    gaps = missing(document, consumed, routes)
    checked = sum(len(v) for v in consumed.values()) + len(routes)
    if gaps:
        print(f"{len(gaps)} of {checked} consumed items missing from {args.document}:", file=out)
        for gap in gaps:
            print(f"  {gap}", file=out)
        return 1
    print(f"all {checked} consumed items present in {args.document}", file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
