"""Test-data key coverage audit: every key a test reads exists in every data file it may read.

Tests read data as ``test_data["search"]["keyword"]``. A key that exists in
one environment's file and not another's fails only when that environment
runs — typically in the lane nobody watches. This scans the test sources for
subscript chains on the configured data names and looks every chain up in each
data file (a base file plus per-environment overlays).

Keys read with ``.get("key", default)`` are exempt: the default is the
author saying the key is optional. A subscript the scan cannot resolve — a
variable key, ``test_data[name]`` — is reported as unresolvable rather than
skipped, because an audit that quietly shrinks to what it can parse reports
full coverage for exactly the cases it never checked.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KeyRef:
    file: str
    line: int
    name: str
    path: tuple[str, ...]  # empty when unresolvable
    resolvable: bool = True

    def __str__(self) -> str:
        shown = "".join(f"[{k!r}]" for k in self.path) if self.resolvable else "[<non-literal>]"
        return f"{self.file}:{self.line}: {self.name}{shown}"


def _chain(node: ast.Subscript) -> tuple[str | None, list[ast.expr]]:
    """The base name and the key nodes of ``base[k1][k2]...``, outermost subscript given."""
    keys: list[ast.expr] = []
    current: ast.expr = node
    while isinstance(current, ast.Subscript):
        keys.append(current.slice)
        current = current.value
    keys.reverse()
    return (current.id if isinstance(current, ast.Name) else None), keys


def scan_sources(paths: Iterable[str | Path], names: Iterable[str]) -> list[KeyRef]:
    """Every outermost subscript chain on one of ``names`` (a ``.get`` with a default is exempt)."""
    names = frozenset(names)
    refs: list[KeyRef] = []
    for raw in paths:
        root = Path(raw)
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for file in files:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            inner: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Subscript):
                    inner.add(id(node.value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript) and id(node) not in inner:
                    base, keys = _chain(node)
                    if base not in names:
                        continue
                    if isinstance(node.ctx, ast.Store):
                        continue
                    literal = [
                        k.value
                        for k in keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
                    ok = len(literal) == len(keys)
                    refs.append(
                        KeyRef(str(file), node.lineno, base, tuple(literal) if ok else (), ok)
                    )
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in names
                    and len(node.args) < 2
                    and not node.keywords
                ):
                    # .get without a default: the author still expects the key.
                    key = node.args[0] if node.args else None
                    ok = isinstance(key, ast.Constant) and isinstance(key.value, str)
                    refs.append(
                        KeyRef(
                            str(file),
                            node.lineno,
                            node.func.value.id,
                            (key.value,) if ok else (),  # type: ignore[union-attr]
                            ok,
                        )
                    )
    return refs


def _lookup(data: Any, path: tuple[str, ...]) -> bool:
    for key in path:
        if not isinstance(data, Mapping) or key not in data:
            return False
        data = data[key]
    return True


def merged(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Deep merge: an environment overlay overrides and extends the base file."""
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = merged(out[key], value)
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class AuditResult:
    refs: tuple[KeyRef, ...]
    missing: tuple[tuple[str, KeyRef], ...]  # (data source, reference)
    unresolvable: tuple[KeyRef, ...]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.unresolvable

    def describe(self) -> str:
        lines = [f"{len(self.refs)} test-data reference(s) checked"]
        lines += [f"  missing in {src}: {ref}" for src, ref in self.missing]
        lines += [f"  unresolvable: {ref}" for ref in self.unresolvable]
        return "\n".join(lines)


def audit(
    sources: Iterable[str | Path],
    data_files: Iterable[str | Path],
    names: Iterable[str] = ("test_data",),
    overlays: Iterable[str | Path] = (),
) -> AuditResult:
    """Check every reference against each data file, alone and merged with each overlay."""
    refs = scan_sources(sources, names)
    datasets: list[tuple[str, Any]] = []
    for file in data_files:
        base = json.loads(Path(file).read_text(encoding="utf-8"))
        datasets.append((str(file), base))
        for overlay in overlays:
            extra = json.loads(Path(overlay).read_text(encoding="utf-8"))
            datasets.append((f"{file} + {overlay}", merged(base, extra)))
    missing = tuple(
        (label, ref)
        for ref in refs
        if ref.resolvable
        for label, data in datasets
        if not _lookup(data, ref.path)
    )
    return AuditResult(tuple(refs), missing, tuple(r for r in refs if not r.resolvable))
