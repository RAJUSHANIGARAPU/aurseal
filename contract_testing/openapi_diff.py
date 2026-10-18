"""Breaking-change classification between two OpenAPI 3 documents.

The inputs are plain OpenAPI files, so the gate works for a provider written in
any language. The rules follow the stored-contract differ (a response property a
client relies on must not disappear or change type) and extend them to the
request side, which that differ does not look at.

BREAKING (an existing client can stop working):

* a path or an operation removed;
* a success status code removed from an operation;
* response: a property removed, its type changed, a required property made
  optional, an enum widened (a client switching over the values meets a new one);
* request: a new required property or parameter, an optional one made required,
  a property or parameter type changed, an enum narrowed (values clients send
  are now refused), a body made required.

NON-BREAKING: everything additive — new paths, operations, status codes,
optional request properties/parameters, response properties.

Only what the spec says is compared; undocumented behaviour changes pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BREAKING = "breaking"
NON_BREAKING = "non_breaking"
_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")


@dataclass(frozen=True)
class Change:
    kind: str  # breaking | non_breaking
    location: str
    detail: str
    rule: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.location}: {self.detail}"


class _Resolver:
    def __init__(self, spec: Mapping[str, Any]) -> None:
        self.spec = spec

    def __call__(self, node: Any) -> Any:
        hops = 0
        while isinstance(node, Mapping) and "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                raise ValueError(f"only local $ref is supported, got {ref!r}")
            target: Any = self.spec
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            node = target
            hops += 1
            if hops > 32:
                raise ValueError(f"$ref cycle at {ref}")
        return node


def _type(schema: Mapping[str, Any]) -> str | None:
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(sorted(str(k) for k in kind))
    return kind


def _json_schema(container: Mapping[str, Any] | None, resolve: _Resolver) -> Mapping | None:
    content = (container or {}).get("content") or {}
    for media, body in content.items():
        if "json" in media:
            return resolve((body or {}).get("schema") or {})
    return None


class _Differ:
    def __init__(self, old: Mapping[str, Any], new: Mapping[str, Any]) -> None:
        self.old_spec, self.new_spec = old, new
        self.r_old, self.r_new = _Resolver(old), _Resolver(new)
        self.changes: list[Change] = []

    def add(self, kind: str, location: str, detail: str, rule: str) -> None:
        self.changes.append(Change(kind, location, detail, rule))

    def run(self) -> list[Change]:
        old_paths = self.old_spec.get("paths") or {}
        new_paths = self.new_spec.get("paths") or {}
        for path in sorted(old_paths.keys() - new_paths.keys()):
            self.add(BREAKING, path, "path removed", "path_removed")
        for path in sorted(new_paths.keys() - old_paths.keys()):
            self.add(NON_BREAKING, path, "path added", "path_added")
        for path in sorted(old_paths.keys() & new_paths.keys()):
            self.path_item(path, self.r_old(old_paths[path]), self.r_new(new_paths[path]))
        return self.changes

    def path_item(self, path: str, old: Mapping, new: Mapping) -> None:
        shared_old = old.get("parameters") or []
        shared_new = new.get("parameters") or []
        for method in _METHODS:
            where = f"{method.upper()} {path}"
            if method in old and method not in new:
                self.add(BREAKING, where, "operation removed", "operation_removed")
            elif method in new and method not in old:
                self.add(NON_BREAKING, where, "operation added", "operation_added")
            elif method in old:
                o, n = self.r_old(old[method]), self.r_new(new[method])
                self.parameters(
                    where,
                    [*shared_old, *(o.get("parameters") or [])],
                    [*shared_new, *(n.get("parameters") or [])],
                )
                self.request_body(where, o.get("requestBody"), n.get("requestBody"))
                self.responses(where, o.get("responses") or {}, n.get("responses") or {})

    # -- request side ---------------------------------------------------------

    def parameters(self, where: str, old: list, new: list) -> None:
        def index(params, resolve):
            out = {}
            for p in params:
                p = resolve(p)
                out[(p.get("in"), p.get("name"))] = p
            return out

        o, n = index(old, self.r_old), index(new, self.r_new)
        for key in sorted(n.keys() - o.keys(), key=str):
            if n[key].get("required"):
                self.add(BREAKING, f"{where} param {key[1]}", "new required parameter", "req_param")
            else:
                self.add(
                    NON_BREAKING, f"{where} param {key[1]}", "new optional parameter", "opt_param"
                )
        for key in sorted(o.keys() - n.keys(), key=str):
            self.add(NON_BREAKING, f"{where} param {key[1]}", "parameter removed", "param_removed")
        for key in sorted(o.keys() & n.keys(), key=str):
            loc = f"{where} param {key[1]}"
            if n[key].get("required") and not o[key].get("required"):
                self.add(BREAKING, loc, "parameter became required", "param_required")
            self.schema(
                loc,
                self.r_old(o[key].get("schema") or {}),
                self.r_new(n[key].get("schema") or {}),
                request=True,
            )

    def request_body(self, where: str, old: Any, new: Any) -> None:
        old = self.r_old(old) if old else None
        new = self.r_new(new) if new else None
        if new and new.get("required") and not (old and old.get("required")):
            self.add(BREAKING, f"{where} request", "request body became required", "body_required")
        o = _json_schema(old, self.r_old)
        n = _json_schema(new, self.r_new)
        if o is not None and n is not None:
            self.schema(f"{where} request", o, n, request=True)

    # -- response side --------------------------------------------------------

    def responses(self, where: str, old: Mapping, new: Mapping) -> None:
        for code in sorted(old.keys() - new.keys(), key=str):
            if str(code).startswith("2"):
                self.add(BREAKING, f"{where} {code}", "success status removed", "status_removed")
            else:
                self.add(NON_BREAKING, f"{where} {code}", "status removed", "status_removed")
        for code in sorted(new.keys() - old.keys(), key=str):
            self.add(NON_BREAKING, f"{where} {code}", "status added", "status_added")
        for code in sorted(old.keys() & new.keys(), key=str):
            o = _json_schema(self.r_old(old[code]), self.r_old)
            n = _json_schema(self.r_new(new[code]), self.r_new)
            if o and n:
                self.schema(f"{where} {code}", o, n, request=False)
            elif o and n is None:
                self.add(BREAKING, f"{where} {code}", "JSON response body removed", "body_removed")

    # -- schemas --------------------------------------------------------------

    def schema(self, where: str, old: Mapping, new: Mapping, *, request: bool) -> None:
        old, new = self.r_old(old), self.r_new(new)
        if not old or not new:
            return
        ot, nt = _type(old), _type(new)
        if ot and nt and ot != nt:
            self.add(BREAKING, where, f"type changed {ot} -> {nt}", "type_changed")
            return
        self.enum(where, old, new, request=request)
        if (ot or nt) == "array":
            items_old, items_new = old.get("items") or {}, new.get("items") or {}
            self.schema(f"{where}[]", items_old, items_new, request=request)
            return
        old_props = old.get("properties") or {}
        new_props = new.get("properties") or {}
        if not old_props and not new_props:
            return
        old_req, new_req = set(old.get("required") or []), set(new.get("required") or [])
        for name in sorted(new_props.keys() - old_props.keys()):
            loc = f"{where}.{name}"
            if request and name in new_req:
                self.add(BREAKING, loc, "new required request property", "req_property")
            else:
                self.add(NON_BREAKING, loc, "property added", "property_added")
        for name in sorted(old_props.keys() - new_props.keys()):
            loc = f"{where}.{name}"
            if request:
                self.add(NON_BREAKING, loc, "request property removed", "property_removed")
            else:
                self.add(BREAKING, loc, "response property removed", "property_removed")
        for name in sorted(old_props.keys() & new_props.keys()):
            loc = f"{where}.{name}"
            if request and name in new_req and name not in old_req:
                self.add(BREAKING, loc, "request property became required", "became_required")
            if not request and name in old_req and name not in new_req:
                self.add(BREAKING, loc, "response property no longer required", "became_optional")
            self.schema(loc, old_props[name], new_props[name], request=request)

    def enum(self, where: str, old: Mapping, new: Mapping, *, request: bool) -> None:
        if "enum" not in old or "enum" not in new:
            if request and "enum" in new and "enum" not in old:
                self.add(BREAKING, where, "request value restricted to an enum", "enum_added")
            return
        removed = [v for v in old["enum"] if v not in new["enum"]]
        added = [v for v in new["enum"] if v not in old["enum"]]
        if request and removed:
            self.add(BREAKING, where, f"enum narrowed, removed {removed}", "enum_narrowed")
        elif not request and added:
            self.add(BREAKING, where, f"response enum widened, added {added}", "enum_widened")
        elif removed or added:
            self.add(NON_BREAKING, where, f"enum changed +{added} -{removed}", "enum_changed")


def diff_openapi(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[Change]:
    """Every change between two OpenAPI 3 documents, classified."""
    for name, doc in (("old", old), ("new", new)):
        if not isinstance(doc, Mapping) or "paths" not in doc:
            raise ValueError(f"{name} document is not an OpenAPI document (no 'paths')")
    return _Differ(old, new).run()
