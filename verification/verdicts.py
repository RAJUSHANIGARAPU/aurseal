"""Run-wide verdict record: one JSON line per detector or coverage outcome.

A test's pass/fail cannot say *what* a run proved. A skipped detector exits 0
exactly like a confirmed one, and a coverage test that only wrote a figure down
looks the same as one that asserted it. Every expected-red detector and every
coverage verdict therefore appends one line here, so "we could not tell" is a
countable state instead of the same green as "nothing changed".

Contract (read by the run-trust gate; keep it stable)
---------------------------------------------------------
File: ``$VERDICTS_FILE``, default ``reports/verdicts/verdicts.jsonl``. Appended,
one JSON object per line, one ``write`` per record on an ``O_APPEND`` handle, so
xdist workers can share the file without interleaving partial lines.

Fields:

=========  ==================================================================
run_id     ``os.environ.get("TEST_RUN_ID", "")`` at record time
nodeid     pytest node id of the test that produced the verdict
kind       ``"expected_red"`` or ``"coverage"``
ticket     defect id (expected_red) or the optional ticket a coverage subject
           traces to; ``null`` when there is none
verdict    see below
detail     the message behind the verdict, whitespace-flattened, <= 500 chars
=========  ==================================================================

``subject`` (what the verdict is about: the ticket for a detector, the
requirement/row id for coverage) and ``timestamp`` (UTC ISO-8601) are written
too. Readers must ignore keys they do not know.

expected_red verdicts:

==================  ==========================================================
reproduces          oracle failed with the documented signature: still broken
cleared             oracle passed on an open defect: looks fixed, unwrap it
held_empty       the positive control or a precondition failed: no verdict
signature_mismatch  oracle failed, but not because of the tracked defect
closed_pass         defect is closed and the oracle passed: fix holds
closed_regressed    defect is closed and it reproduces again
==================  ==========================================================

coverage verdicts: ``proved`` (exercised and asserted: the only release
evidence), ``recorded`` (observed, not asserted), ``held_empty`` (passed only
because the data was missing), ``abstained`` (precondition not met, no verdict).

``read_verdicts(path=None, run_id=None)`` is the reading side; it tolerates a
missing file and skips malformed lines.

Recording never raises: a reporting problem must not change a test's outcome.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VERDICTS_FILE_ENV = "VERDICTS_FILE"
RUN_ID_ENV = "TEST_RUN_ID"
DEFAULT_VERDICTS_FILE = Path("reports") / "verdicts" / "verdicts.jsonl"
DETAIL_LIMIT = 500


class Kind(str, Enum):
    EXPECTED_RED = "expected_red"
    COVERAGE = "coverage"
    CONTRACT = "contract"


class RedVerdict(str, Enum):
    REPRODUCES = "reproduces"
    CLEARED = "cleared"
    HELD_EMPTY = "held_empty"
    SIGNATURE_MISMATCH = "signature_mismatch"
    CLOSED_PASS = "closed_pass"
    CLOSED_REGRESSED = "closed_regressed"


class CoverageOutcome(str, Enum):
    PROVED = "proved"
    RECORDED = "recorded"
    HELD_EMPTY = "held_empty"
    ABSTAINED = "abstained"


class ContractVerdict(str, Enum):
    """The contract gate's answer for one consumer/provider pair or spec pair."""

    COMPATIBLE = "compatible"
    BREAKING = "breaking"
    PROVIDER_MISMATCH = "provider_mismatch"
    NO_EVIDENCE = "no_evidence"


_VOCABULARY: dict[Kind, type[Enum]] = {
    Kind.EXPECTED_RED: RedVerdict,
    Kind.COVERAGE: CoverageOutcome,
    Kind.CONTRACT: ContractVerdict,
}


_suppressed = threading.local()
_CURRENT_TEST = re.compile(r"\s+\((setup|call|teardown)\)$")


def verdicts_path(path: str | os.PathLike[str] | None = None) -> Path:
    """The file to append to / read from: ``path``, else ``$VERDICTS_FILE``, else the default."""
    if path is not None:
        return Path(path)
    return Path(os.environ.get(VERDICTS_FILE_ENV) or DEFAULT_VERDICTS_FILE)


def current_nodeid() -> str:
    """The running test's node id, from ``PYTEST_CURRENT_TEST``; ``""`` outside a test."""
    return _CURRENT_TEST.sub("", os.environ.get("PYTEST_CURRENT_TEST", ""))


def flatten(detail: object, limit: int = DETAIL_LIMIT) -> str:
    """One readable line: whitespace collapsed, cut at ``limit`` characters."""
    flat = " ".join(str(detail or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


@contextmanager
def suppress_recording() -> Iterator[None]:
    """Record nothing inside the block (this thread only).

    For the verdict machinery's own self-tests: they drive every branch on
    purpose, and a synthetic "BUG-1 cleared" must never land in the evidence
    file next to real verdicts.
    """
    previous = getattr(_suppressed, "on", False)
    _suppressed.on = True
    try:
        yield
    finally:
        _suppressed.on = previous


def record_verdict(
    kind: Kind | str,
    verdict: RedVerdict | CoverageOutcome | ContractVerdict | str,
    *,
    ticket: str | None = None,
    subject: str | None = None,
    detail: object = "",
    nodeid: str | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Append one verdict line and return it; ``None`` when suppressed or unwritable.

    An unknown ``kind`` or a verdict outside that kind's vocabulary raises
    ``ValueError``: that is a caller bug, not a reporting problem.
    """
    kind = Kind(kind)
    allowed = _VOCABULARY[kind]
    verdict = allowed(_value(verdict))
    if getattr(_suppressed, "on", False):
        return None
    entry: dict[str, Any] = {
        "run_id": os.environ.get(RUN_ID_ENV, ""),
        "nodeid": nodeid if nodeid is not None else current_nodeid(),
        "kind": kind.value,
        "ticket": ticket,
        "verdict": verdict.value,
        "detail": flatten(detail),
        "subject": subject if subject is not None else ticket,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    target = verdicts_path(path)
    line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("[verdicts] could not record %s for %s: %s", entry["verdict"], ticket, exc)
        return None
    return entry


def read_verdicts(
    path: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
    *,
    start: int = 0,
) -> list[dict[str, Any]]:
    """Every record in the file, optionally only one run's.

    ``start`` is a byte offset to read from, so a session can read only the
    lines appended since it began. A missing file is an empty list; a malformed
    line (a crashed writer, a hand edit) is skipped with a warning rather than
    hiding every other verdict behind one exception.
    """
    target = verdicts_path(path)
    try:
        with target.open("rb") as handle:
            handle.seek(start)
            raw = handle.read()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[verdicts] skipping malformed line %d in %s", number, target)
            continue
        if not isinstance(entry, dict):
            continue
        if run_id is not None and entry.get("run_id") != run_id:
            continue
        records.append(entry)
    return records


def file_size(path: str | os.PathLike[str] | None = None) -> int:
    """Current size of the verdict file, 0 when absent: the offset a session starts at."""
    try:
        return verdicts_path(path).stat().st_size
    except OSError:
        return 0


def _value(verdict: RedVerdict | CoverageOutcome | str) -> str:
    return verdict.value if isinstance(verdict, Enum) else str(verdict)
