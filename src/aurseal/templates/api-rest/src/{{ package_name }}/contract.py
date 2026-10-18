"""JSON Schema contracts: catch drift a pydantic model would silently coerce away."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


@cache
def validator(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def check(name: str, payload: Any) -> None:
    """Raise ``jsonschema.ValidationError`` when ``payload`` breaks schema ``name``."""
    validator(name).validate(payload)
