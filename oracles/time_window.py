"""Keep only the records produced on or after a moment, such as a deploy.

After a deploy, a check about the new build must not judge output the old
build wrote. :func:`produced_since` selects records of one type whose
timestamp is on or after the cut-off. Timestamps with different offsets
(``+02:00``, ``Z``) are compared as instants.

A timestamp without an offset is an error, never assumed to be local time: a
wrong guess silently moves a record across the cut-off. A record of the
wanted type whose timestamp is missing or unreadable is an error too, because
dropping it would hide exactly the record a test may be looking for.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any


class TimestampError(ValueError):
    """A timestamp is missing, unreadable or has no offset."""


def parse_instant(text: Any) -> datetime:
    """An offset-aware datetime from ISO 8601 text (``Z`` accepted)."""
    if not isinstance(text, str) or not text.strip():
        raise TimestampError(f"no timestamp: {text!r}")
    raw = text.strip()
    try:
        value = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    except ValueError as exc:
        raise TimestampError(f"unreadable timestamp {text!r}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise TimestampError(f"timestamp {text!r} has no offset; refusing to guess a timezone")
    return value


def produced_since(
    records: Iterable[Mapping[str, Any]],
    record_type: str | None,
    since: datetime,
    *,
    type_key: str = "type",
    time_key: str = "created_at",
) -> list[Mapping[str, Any]]:
    """Records of ``record_type`` (all types when ``None``) with a timestamp ``>= since``."""
    if since.tzinfo is None or since.utcoffset() is None:
        raise TimestampError(f"cut-off {since!r} is naive; pass a timezone-aware datetime")
    selected = []
    for record in records:
        if record_type is not None and record.get(type_key) != record_type:
            continue
        try:
            stamp = parse_instant(record.get(time_key))
        except TimestampError as exc:
            raise TimestampError(f"{exc} in record {dict(record)}") from exc
        if stamp >= since:
            selected.append(record)
    return selected
