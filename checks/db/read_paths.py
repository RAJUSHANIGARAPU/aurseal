"""Read-only helpers for common checks. A source test keeps writer usage out of this file.

Identifiers (table and column names) are validated, never interpolated from
untrusted text; values are always bound parameters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from checks.db.probe import DbProbe, QuerySet

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"not a plain SQL identifier: {name!r}")
    return name


def count_rows(probe: DbProbe, table: str, where: Mapping[str, Any] | None = None) -> int:
    sql, params = _select(table, ("COUNT(*) AS n",), where)
    return int(probe.query(sql, params)[0]["n"])


def rows_where(
    probe: DbProbe,
    table: str,
    columns: Iterable[str],
    where: Mapping[str, Any] | None = None,
    order_by: str = "id",
) -> list[dict]:
    sql, params = _select(table, tuple(ident(c) for c in columns), where)
    return probe.query(f"{sql} ORDER BY {ident(order_by)}", params)


def tagged_query_set(
    tables: Mapping[str, Iterable[str]], tag_column: str, tag_param: str = "tag"
) -> QuerySet:
    """One query per table, scoped to rows carrying the run tag (keeps snapshots small)."""
    out: dict[str, str] = {}
    for table, columns in tables.items():
        cols = ", ".join(ident(c) for c in columns)
        out[table] = (
            f"SELECT {cols} FROM {ident(table)} WHERE {ident(tag_column)} = :{ident(tag_param)} "
            f"ORDER BY id"
        )
    return out


def _select(
    table: str, columns: tuple[str, ...], where: Mapping[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    sql = f"SELECT {', '.join(columns)} FROM {ident(table)}"
    params: dict[str, Any] = {}
    if where:
        clauses = []
        for i, (column, value) in enumerate(where.items()):
            clauses.append(f"{ident(column)} = :w{i}")
            params[f"w{i}"] = value
        sql += " WHERE " + " AND ".join(clauses)
    return sql, params
