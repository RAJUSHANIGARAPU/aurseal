"""Constraint profiles: what a valid record of an entity looks like, as plain data.

A profile lists an entity's fields and the constraints each must satisfy. It is a
JSON file (schema ``schemas/synthetic-profile.schema.json``) so a framework in
another language can author one, or it is derived from an OpenAPI request schema
with :meth:`Profile.from_openapi`.

:meth:`Profile.check` is the single judge of a record: it returns every
constraint the record breaks, named ``<field>.<constraint>`` (``name.max_len``,
``email.format``, ``kind.choices``...). The generator uses it to prove its own
output — a ``valid`` record breaks nothing, an ``invalid`` record breaks exactly
the constraint it is labelled with.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

KINDS = ("string", "email", "integer", "number", "boolean", "choice", "date")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COMBINATORS = ("oneOf", "anyOf", "allOf", "not")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str = "string"
    required: bool = False
    min_len: int | None = None
    max_len: int | None = None
    pattern: str | None = None
    choices: tuple[Any, ...] = ()
    min: float | None = None
    max: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"field {self.name!r}: kind {self.kind!r} not in {KINDS}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"field {self.name!r}: a choice field needs choices")
        if self.pattern is not None:
            re.compile(self.pattern)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FieldSpec:
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        if "choices" in known:
            known["choices"] = tuple(known["choices"])
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "kind": self.kind}
        for key in ("required", "min_len", "max_len", "pattern", "min", "max"):
            value = getattr(self, key)
            if value not in (None, False):
                out[key] = value
        if self.choices:
            out["choices"] = list(self.choices)
        return out


def _type_ok(spec: FieldSpec, value: Any) -> bool:
    if spec.kind in ("string", "email", "date"):
        return isinstance(value, str)
    if spec.kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if spec.kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec.kind == "boolean":
        return isinstance(value, bool)
    return True  # choice: membership is checked separately


def field_violations(spec: FieldSpec, values: Mapping[str, Any]) -> list[str]:
    """Constraints of one field that ``values`` breaks."""
    name = spec.name
    if name not in values or values[name] is None:
        return [f"{name}.required"] if spec.required else []
    value = values[name]
    if not _type_ok(spec, value):
        return [f"{name}.type"]
    broken: list[str] = []
    if spec.kind == "choice" and value not in spec.choices:
        broken.append(f"{name}.choices")
    if isinstance(value, str):
        if spec.min_len is not None and len(value) < spec.min_len:
            broken.append(f"{name}.min_len")
        if spec.max_len is not None and len(value) > spec.max_len:
            broken.append(f"{name}.max_len")
        if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
            broken.append(f"{name}.pattern")
        if spec.kind == "email" and not EMAIL_RE.match(value):
            broken.append(f"{name}.format")
        if spec.kind == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                broken.append(f"{name}.format")
    if spec.kind in ("integer", "number"):
        if spec.min is not None and value < spec.min:
            broken.append(f"{name}.min")
        if spec.max is not None and value > spec.max:
            broken.append(f"{name}.max")
    return broken


@dataclass(frozen=True)
class Profile:
    entity: str
    fields: tuple[FieldSpec, ...]
    endpoint: str | None = None

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"profile {self.entity!r}: duplicate field names")

    def get_field(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def check(self, values: Mapping[str, Any]) -> list[str]:
        """Every constraint ``values`` breaks; ``[]`` for a valid record."""
        return [v for spec in self.fields for v in field_violations(spec, values)]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Profile:
        fields = tuple(FieldSpec.from_dict(f) for f in raw.get("fields") or [])
        if not fields:
            raise ValueError("a profile needs at least one field")
        return cls(
            entity=str(raw.get("entity") or "entity"),
            fields=fields,
            endpoint=raw.get("endpoint"),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> Profile:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"entity": self.entity, "fields": [f.to_dict() for f in self.fields]}
        if self.endpoint:
            out["endpoint"] = self.endpoint
        return out

    @classmethod
    def from_openapi(cls, spec: Mapping[str, Any], operation_id: str) -> Profile:
        """Profile from the JSON request body of one OpenAPI 3 operation.

        Covers JSON-Schema basics: type, enum, minLength/maxLength, pattern,
        minimum/maximum, format email/date, required. Combinators are refused,
        not guessed. Object and array properties are skipped (not profiled).
        """
        for path, operations in (spec.get("paths") or {}).items():
            for method, operation in (operations or {}).items():
                if isinstance(operation, Mapping) and operation.get("operationId") == operation_id:
                    schema = (
                        ((operation.get("requestBody") or {}).get("content") or {})
                        .get("application/json", {})
                        .get("schema")
                    )
                    if not schema:
                        raise ValueError(f"{operation_id}: no JSON request body schema")
                    schema = _resolve(spec, schema)
                    return cls(
                        entity=operation_id,
                        fields=_fields_from_schema(spec, schema, operation_id),
                        endpoint=path if method.lower() == "post" else None,
                    )
        raise KeyError(f"operationId {operation_id!r} not found")


def _resolve(spec: Mapping[str, Any], schema: Mapping[str, Any]) -> Mapping[str, Any]:
    seen = 0
    while "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported, got {ref!r}")
        node: Any = spec
        for part in ref[2:].split("/"):
            node = node[part]
        schema = node
        seen += 1
        if seen > 20:
            raise ValueError("$ref cycle")
    return schema


def _fields_from_schema(
    spec: Mapping[str, Any], schema: Mapping[str, Any], where: str
) -> tuple[FieldSpec, ...]:
    if any(k in schema for k in _COMBINATORS):
        raise ValueError(f"{where}: schema combinators are not supported")
    required = set(schema.get("required") or [])
    fields: list[FieldSpec] = []
    for name, prop in (schema.get("properties") or {}).items():
        prop = _resolve(spec, prop)
        if any(k in prop for k in _COMBINATORS):
            raise ValueError(f"{where}.{name}: schema combinators are not supported")
        kind = _kind(prop)
        if kind is None:
            continue
        fields.append(
            FieldSpec(
                name=name,
                kind=kind,
                required=name in required,
                min_len=prop.get("minLength"),
                max_len=prop.get("maxLength"),
                pattern=prop.get("pattern"),
                choices=tuple(prop.get("enum") or ()),
                min=prop.get("minimum"),
                max=prop.get("maximum"),
            )
        )
    if not fields:
        raise ValueError(f"{where}: no profilable properties")
    return tuple(fields)


def _kind(prop: Mapping[str, Any]) -> str | None:
    if prop.get("enum"):
        return "choice"
    kind = prop.get("type")
    if kind == "string":
        return {"email": "email", "date": "date"}.get(prop.get("format", ""), "string")
    if kind in ("integer", "number", "boolean"):
        return kind
    return None
