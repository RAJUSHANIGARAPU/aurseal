"""Small helpers shared by the traceability modules: paths, JSON/YAML files, schemas."""

from __future__ import annotations

import json
import os
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from results._util import utc_now

DIR_ENV = "TRACEABILITY_DIR"
DEFAULT_DIR = Path("reports") / "traceability"
SCHEMAS = Path(__file__).resolve().parent / "schemas"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

__all__ = [
    "SchemaError",
    "env_flag",
    "load_yaml",
    "read_json",
    "trace_dir",
    "utc_now",
    "validate",
    "write_json",
    "write_text",
]


class SchemaError(ValueError):
    """A document does not match its JSON Schema."""


def env_flag(name: str, default: bool = False, env: dict[str, str] | None = None) -> bool:
    raw = (env if env is not None else os.environ).get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUTHY


def trace_dir() -> Path:
    """Where run-level traceability files go: ``$TRACEABILITY_DIR``, else reports/traceability."""
    return Path(os.environ.get(DIR_ENV) or DEFAULT_DIR)


def write_text(path: Path, text: str) -> Path:
    """Write atomically (temp file + rename) so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def write_json(path: Path, data: Any) -> Path:
    return write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_yaml(path: Path) -> Any:
    """Parse a YAML file (safe loader). Raises ``ValueError`` for unreadable YAML."""
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc


@cache
def _schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def validate(document: Any, schema_name: str) -> None:
    """Raise :class:`SchemaError` listing every violation of ``schemas/<schema_name>``."""
    import jsonschema

    validator = jsonschema.Draft202012Validator(_schema(schema_name))
    problems = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if problems:
        lines = [
            f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in problems
        ]
        raise SchemaError(f"{schema_name}: " + "; ".join(lines))
