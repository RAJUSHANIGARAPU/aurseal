"""Audit-trail release check: request an export for the window since the deploy and judge it.

The flow a release check runs, with the controls that keep it from passing
on nothing:

1. :func:`export_window` requests an export for ``[start, end]``, polls the
   export list until the export reaches a terminal status, downloads and
   parses it. A failed or cancelled export raises; a timeout raises.
2. :func:`require_own_activity` is the control: the export must hold the
   service's own activity in the window. An export that holds nothing at all
   cannot distinguish "nothing was audited" from "the audit trail is broken".
3. :func:`events` matches by structured category and event codes (never by
   free text) and :func:`check_events` checks the actor of every match and
   abstains when the window holds no such event.
4. :func:`secret_like` flags rows that carry a credential-looking field with a
   value, using the same name rule as contract redaction
   (:func:`contract_testing.redaction.is_sensitive_name`).

The archive is read by :func:`read_export_rows`: every ``.json`` entry (a list
of objects) and every ``.csv`` entry (via :mod:`oracles.activity_export`).
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import Callable, Collection, Iterable, Mapping
from datetime import datetime
from typing import Any

from contract_testing.redaction import REDACTED, is_sensitive_name
from oracles.activity_export import ExportFormatError, read_rows
from verification.evidence_verdict import EvidenceVerdict

Row = Mapping[str, Any]
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
SUCCEEDED = "COMPLETED"


class ExportFailed(AssertionError):
    """The export reached a terminal status other than success."""


class ExportTimeout(AssertionError):
    """The export did not reach a terminal status in time."""


class OwnActivityMissing(AssertionError):
    """The control failed: the export holds none of the service's own activity."""


def read_export_rows(archive: bytes) -> list[dict[str, Any]]:
    """Every row of every ``.json`` / ``.csv`` entry in the zip, in entry order."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise ExportFormatError(f"not a zip archive ({len(archive)} bytes)") from exc
    rows: list[dict[str, Any]] = []
    with zf:
        names = zf.namelist()
        for name in names:
            lowered = name.lower()
            if lowered.endswith(".json"):
                data = json.loads(zf.read(name).decode("utf-8-sig"))
                if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                    raise ExportFormatError(f"{name} is not a list of objects")
                rows.extend(data)
            elif lowered.endswith(".csv"):
                rows.extend(read_rows(archive, name))
    if not any(n.lower().endswith((".json", ".csv")) for n in names):
        raise ExportFormatError(f"no .json or .csv entry in the archive; entries: {names}")
    return rows


def export_window(
    engine: Any,
    start: datetime,
    end: datetime,
    *,
    create: Callable[[Any, datetime, datetime], str],
    list: Callable[[Any], Iterable[Row]],  # noqa: A002 - the spec's name for the lister
    download: Callable[[Any, str], bytes],
    terminal: Collection[str] = TERMINAL,
    succeeded: str = SUCCEEDED,
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    id_of: Callable[[Row], Any] = lambda r: r.get("id"),
    status_of: Callable[[Row], Any] = lambda r: r.get("status"),
    parse: Callable[[bytes], list[dict[str, Any]]] = read_export_rows,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[dict[str, Any]]:
    """Request, poll to a terminal status, download and parse one export."""
    for moment in (start, end):
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(f"export window bound {moment!r} is naive")
    if end < start:
        raise ValueError(f"export window ends ({end}) before it starts ({start})")
    if succeeded not in terminal:
        raise ValueError(f"success status {succeeded!r} is not among the terminal {terminal}")
    export_id = str(create(engine, start, end))
    deadline = monotonic() + timeout_s
    last: Any = None
    while True:
        row = next((r for r in list(engine) if str(id_of(r)) == export_id), None)
        last = status_of(row) if row is not None else None
        if last in terminal:
            break
        if monotonic() >= deadline:
            raise ExportTimeout(
                f"export {export_id} still {last!r} after {timeout_s}s "
                f"(terminal: {sorted(terminal)})"
            )
        sleep(poll_s)
    if last != succeeded:
        raise ExportFailed(f"export {export_id} ended {last!r}, not {succeeded!r}")
    return parse(download(engine, export_id))


def require_own_activity(
    rows: Iterable[Row],
    app: str,
    *,
    app_of: Callable[[Row], Any] = lambda r: r.get("application"),
) -> list[Row]:
    """The rows ``app`` produced; raises :class:`OwnActivityMissing` when there are none."""
    rows = list(rows)
    own = [r for r in rows if app_of(r) == app]
    if not own:
        raise OwnActivityMissing(
            f"the export holds {len(rows)} rows and none from {app!r}: the service's own "
            "activity is missing, so an empty match below would not mean 'nothing audited'"
        )
    return own


def events(
    rows: Iterable[Row],
    category: str,
    event: str,
    *,
    category_of: Callable[[Row], Any] = lambda r: r.get("category"),
    event_of: Callable[[Row], Any] = lambda r: r.get("event"),
) -> list[Row]:
    """Rows whose category and event codes equal ``category`` / ``event`` exactly."""
    return [r for r in rows if category_of(r) == category and event_of(r) == event]


def _flatten(row: Row, prefix: str = "") -> Iterable[tuple[str, Any]]:
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            yield from _flatten(value, f"{name}.")
        else:
            yield name, value


def secret_like(row: Row) -> list[str]:
    """Field names (dotted for nested objects) that look like credentials and carry a value."""
    hits = []
    for name, value in _flatten(row):
        leaf = name.rsplit(".", 1)[-1]
        if not is_sensitive_name(leaf):
            continue
        if value in (None, "", REDACTED) or (isinstance(value, str) and set(value) <= {"*"}):
            continue
        hits.append(name)
    return hits


def check_events(
    rows: Iterable[Row],
    *,
    category: str,
    event: str,
    actor: str,
    actor_of: Callable[[Row], Any] = lambda r: r.get("actor"),
    category_of: Callable[[Row], Any] = lambda r: r.get("category"),
    event_of: Callable[[Row], Any] = lambda r: r.get("event"),
    verdict: EvidenceVerdict | None = None,
) -> list[str]:
    """Problems with the matching events: wrong actor, secret-like content.

    Abstains when the window holds no ``category``/``event`` row: nothing
    happened that the check is about.
    """
    rows = list(rows)
    matched = events(rows, category, event, category_of=category_of, event_of=event_of)
    if not matched:
        coverage = verdict or EvidenceVerdict(f"audit trail {category}/{event}")
        coverage.no_verdict(
            f"no {category}/{event} event in the window ({len(rows)} rows read); nothing to judge"
        )
    problems = []
    for row in matched:
        if actor_of(row) != actor:
            problems.append(f"{category}/{event} by {actor_of(row)!r}, expected {actor!r}: {row}")
    for row in rows:
        secrets = secret_like(row)
        if secrets:
            problems.append(f"secret-like content in {secrets}: row {row.get('id', '?')}")
    return problems
