"""Tolerant reader for detector verdict rows (one JSON object per line).

Schema, shared with the code that writes the rows::

    {"run_id": str, "nodeid": str, "kind": "expected_red" | "coverage",
     "ticket": str | null, "verdict": str, "detail": str}

The run gate only reads these. A line it cannot use — malformed JSON, not an
object, missing ``kind``/``verdict``/``nodeid`` — is dropped and counted rather
than raised: a report that cannot be read says "not recorded", it does not
destroy the run's real result.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from core.logger import logger

ENV = "VERDICTS_FILE"
DEFAULT_PATH = Path("reports") / "verdicts" / "verdicts.jsonl"

EXPECTED_RED = "expected_red"
COVERAGE = "coverage"

# Verdicts that answer the question a detector exists to ask.
REACHED = frozenset({"reproduces", "cleared", "closed_pass", "closed_regressed"})
# Verdicts that are a detector saying nothing (or answering a different question).
SILENT = frozenset({"held_empty", "signature_mismatch"})
COVERAGE_PROVED = "proved"
# A coverage row saying "this test is out of scope here, see covered_by": a skip that is
# not an abstention (gate.requirement.not_applicable writes it).
COVERAGE_NOT_APPLICABLE = "not_applicable"
# Every not-applicable skip reason starts with this, so the run-trust tally can tell it
# from an abstention even when the verdict file was not written.
NOT_APPLICABLE_PREFIX = "not applicable here: "


@dataclass(frozen=True)
class VerdictRow:
    run_id: str | None
    nodeid: str
    kind: str
    ticket: str | None
    verdict: str
    detail: str = ""

    @property
    def key(self) -> str:
        """What one tracked defect is identified by: its ticket, else the test."""
        return self.ticket or self.nodeid


def verdicts_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get(ENV) or DEFAULT_PATH)


def _row(obj: object) -> VerdictRow | None:
    if not isinstance(obj, dict):
        return None
    kind, verdict, nodeid = obj.get("kind"), obj.get("verdict"), obj.get("nodeid")
    if not all(isinstance(v, str) and v for v in (kind, verdict, nodeid)):
        return None
    ticket = obj.get("ticket")
    run_id = obj.get("run_id")
    return VerdictRow(
        run_id=run_id if isinstance(run_id, str) else None,
        nodeid=nodeid,
        kind=kind,
        ticket=ticket if isinstance(ticket, str) and ticket else None,
        verdict=verdict,
        detail=str(obj.get("detail") or ""),
    )


def read_verdict_rows(
    path: str | Path | None = None, run_id: str | None = None
) -> list[VerdictRow]:
    """Rows from ``path`` (default ``$VERDICTS_FILE``), only this run's when ``run_id`` is given."""
    target = verdicts_path(path)
    rows: list[VerdictRow] = []
    dropped = 0
    try:
        if not target.is_file():
            return rows
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("[run-gate] could not read verdicts %s: %s", target, exc)
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = _row(json.loads(line))
        except ValueError:
            row = None
        if row is None:
            dropped += 1
            continue
        if run_id is not None and row.run_id != run_id:
            continue
        rows.append(row)
    if dropped:
        logger.warning("[run-gate] ignored %d unreadable verdict line(s) in %s", dropped, target)
    return rows
