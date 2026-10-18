"""Read an activity-log export archive (a zip holding a CSV) into API call records.

Audit and activity services typically export as a zip with one CSV per
activity type. The CSV follows RFC 4180: quoted fields, doubled quotes and
newlines inside a field, which the stdlib :mod:`csv` reader handles.

Some exporters render a structured column as a ``{key=value, key=value}`` map
instead of separate columns. Splitting that text on commas truncates any value
that contains one (a query string, a list). :func:`parse_map_cell` therefore
tokenises: a comma only separates two entries when what follows it is
``key=``; nested ``{}``/``[]`` and double-quoted values are kept whole.

:class:`ColumnMap` names where each field of :class:`CallRecord` comes from:
either a plain column, or ``(column, key)`` for a key inside a map column.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_ENTRY = "call_log.csv"

Source = str | tuple[str, str]
"""A plain column name, or ``(map column, key)``."""

_KEY_AHEAD = re.compile(r"\s*[A-Za-z_][\w.\-]*\s*=")


class ExportFormatError(ValueError):
    """The archive or CSV does not have the expected shape."""


@dataclass(frozen=True)
class ColumnMap:
    actor: Source = "actor"
    method: Source = "method"
    path: Source = "path"
    status: Source = "status"


@dataclass(frozen=True)
class CallRecord:
    actor: str | None
    method: str | None
    path: str | None
    status: int | None
    row: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)


def parse_map_cell(text: str) -> dict[str, str]:
    """``{a=1, b=x,y, c={d=2}}`` -> ``{"a": "1", "b": "x,y", "c": "{d=2}"}``.

    Outer braces are optional. A value in double quotes may hold anything; the
    quotes are removed. Raises :class:`ExportFormatError` on unbalanced brackets.
    """
    body = text.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    entries: list[str] = []
    depth, quoted, start = 0, False, 0
    for i, ch in enumerate(body):
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                raise ExportFormatError(f"unbalanced brackets in map cell {text!r}")
        elif ch == "," and depth == 0 and _KEY_AHEAD.match(body, i + 1):
            entries.append(body[start:i])
            start = i + 1
    if depth != 0 or quoted:
        raise ExportFormatError(f"unbalanced brackets or quotes in map cell {text!r}")
    entries.append(body[start:])
    result: dict[str, str] = {}
    for entry in entries:
        if not entry.strip():
            continue
        key, sep, value = entry.partition("=")
        if not sep:
            raise ExportFormatError(f"map entry {entry!r} has no '=' in {text!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        result[key.strip()] = value
    return result


def read_rows(archive: bytes, entry: str = DEFAULT_ENTRY) -> list[dict[str, str]]:
    """The rows of ``entry`` in the zip, as dicts keyed by header.

    The entry is matched by exact name first, then by file name ignoring case
    and directories. A missing entry raises with the archive's listing.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise ExportFormatError(f"not a zip archive ({len(archive)} bytes)") from exc
    with zf:
        names = zf.namelist()
        name = entry if entry in names else None
        if name is None:
            wanted = entry.rsplit("/", 1)[-1].casefold()
            matches = [n for n in names if n.rsplit("/", 1)[-1].casefold() == wanted]
            if not matches:
                raise ExportFormatError(f"no entry {entry!r} in archive; entries: {names}")
            name = matches[0]
        text = zf.read(name).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ExportFormatError(f"{name} has no header row")
    return [dict(row) for row in reader]


def _value(row: Mapping[str, str], source: Source, cache: dict[str, dict[str, str]]):
    if isinstance(source, str):
        value = row.get(source)
    else:
        column, key = source
        if column not in cache:
            raw = row.get(column)
            cache[column] = parse_map_cell(raw) if raw else {}
        value = cache[column].get(key)
    return value if value not in (None, "") else None


def read_activity_export(
    archive: bytes, entry: str = DEFAULT_ENTRY, columns: ColumnMap | None = None
) -> list[CallRecord]:
    """Every row of the activity CSV as an :class:`CallRecord`, in file order."""
    columns = columns or ColumnMap()
    activities = []
    for row in read_rows(archive, entry):
        cache: dict[str, dict[str, str]] = {}
        status_raw = _value(row, columns.status, cache)
        try:
            status = int(status_raw) if status_raw is not None else None
        except ValueError as exc:
            raise ExportFormatError(f"status {status_raw!r} is not a number in {row}") from exc
        method = _value(row, columns.method, cache)
        activities.append(
            CallRecord(
                actor=_value(row, columns.actor, cache),
                method=method.upper() if method else None,
                path=_value(row, columns.path, cache),
                status=status,
                row=row,
            )
        )
    return activities
