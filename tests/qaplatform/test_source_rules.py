"""Structural rules the platform's guarantees rest on, checked against the source."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "qaplatform"
OPTIONAL = {"fastapi", "starlette", "uvicorn", "jinja2", "httpx", "pydantic"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_http_adapters_never_touch_the_store():
    for path in (PKG / "http").rglob("*.py"):
        assert not any(
            m == "qaplatform.store" or m.startswith("results") for m in _imports(path)
        ), path


def test_optional_dependencies_are_imported_only_by_adapters():
    allowed = {PKG / "http" / "app.py"}
    allowed |= set((PKG / "http" / "ui").rglob("*.py")) if (PKG / "http" / "ui").is_dir() else set()
    allowed |= {PKG / "capture.py"}
    for path in PKG.rglob("*.py"):
        roots = {m.split(".")[0] for m in _imports(path)}
        if roots & OPTIONAL:
            assert path in allowed, f"{path} imports {roots & OPTIONAL}"


def test_optional_imports_are_lazy():
    """Even the adapters import them inside functions, so importing the module is free."""
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                assert not {n.split(".")[0] for n in names} & OPTIONAL, path


def test_every_platform_query_is_project_scoped():
    for sql in (PKG / "queries").glob("*.sql"):
        text = sql.read_text(encoding="utf-8")
        assert re.search(r"project_id\s*=\s*:project_id", text), sql.name


def test_platform_migrations_only_create_prefixed_tables():
    for sql in (PKG / "migrations").glob("*.sql"):
        for table in re.findall(r"CREATE TABLE (\w+)", sql.read_text(encoding="utf-8")):
            assert table.startswith("qap_"), (sql.name, table)
