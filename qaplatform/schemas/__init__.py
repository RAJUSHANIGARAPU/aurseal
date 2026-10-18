"""The external contract: JSON Schemas (draft 2020-12) under ``schemas/v1/``.

The schemas, not the Python code and not a web framework's generated document, are what
a client in any language integrates against. ``validate`` checks a payload offline;
the service validates every request body with the same function, so a payload that
passes here is a payload the server accepts.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
API_VERSION = "v1"
SCHEMA_DIR = Path(__file__).resolve().parent / API_VERSION
ID_BASE = "https://example.test/schemas/v1/"


class SchemaError(ValueError):
    """A payload does not match its schema; ``errors`` lists ``{pointer, message}``."""

    def __init__(self, name: str, errors: list[dict[str, str]]) -> None:
        first = errors[0] if errors else {"pointer": "", "message": "invalid"}
        super().__init__(f"{name}: {first['pointer'] or '(root)'}: {first['message']}")
        self.name = name
        self.errors = errors


def _file_name(name: str) -> str:
    base = Path(name).name
    if base.endswith(".schema.json"):
        return base
    if base.endswith(".json"):
        base = base[: -len(".json")]
    if base.endswith(".schema"):
        base = base[: -len(".schema")]
    return f"{base}.schema.json"


def names() -> list[str]:
    """Every schema name (``run-create``, ``test-result``, ...)."""
    return sorted(p.name[: -len(".schema.json")] for p in SCHEMA_DIR.glob("*.schema.json"))


def load(name: str) -> dict[str, Any]:
    """The raw schema document; ``FileNotFoundError`` for an unknown name."""
    path = SCHEMA_DIR / _file_name(name)
    if not path.is_file():
        raise FileNotFoundError(f"no schema named {name!r}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _registry() -> Any:
    from referencing import Registry, Resource

    resources = []
    for n in names():
        doc = load(n)
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


def is_datetime(value: object) -> bool:
    """RFC 3339 date-time with a zone (``2026-01-05T10:12:00Z``); checked without extra deps."""
    if not isinstance(value, str):
        return True  # type mismatches are the "type" keyword's job
    if "T" not in value.upper() or not (value.endswith(("Z", "z")) or value[-6] in "+-"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
    except ValueError:
        return False
    return True


@lru_cache(maxsize=32)
def _validator(name: str) -> Any:
    from jsonschema import Draft202012Validator, FormatChecker

    checker = FormatChecker(formats=())
    checker.checks("date-time")(is_datetime)
    return Draft202012Validator(load(name), registry=_registry(), format_checker=checker)


def errors_for(name: str, payload: Any) -> list[dict[str, str]]:
    """Every violation, as ``{pointer, message}`` (JSON pointer into the payload)."""
    out = []
    for err in sorted(_validator(name).iter_errors(payload), key=lambda e: list(e.path)):
        pointer = "".join(f"/{p}" for p in err.path)
        out.append({"pointer": pointer, "message": err.message})
    return out


def validate(name: str, payload: Any) -> None:
    """Raise :class:`SchemaError` unless ``payload`` matches schema ``name``."""
    errs = errors_for(name, payload)
    if errs:
        raise SchemaError(name, errs)


__all__ = [
    "API_VERSION",
    "ID_BASE",
    "SCHEMA_DIR",
    "SCHEMA_VERSION",
    "SchemaError",
    "errors_for",
    "load",
    "names",
    "validate",
]
