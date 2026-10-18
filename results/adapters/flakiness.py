"""Feed store history to the existing flakiness detector without rewriting it.

``history_from_store`` yields the detector's own ``FlakRecord`` shape: every attempt
that is not a test's final one becomes ``rerun`` (the value pytest-rerunfailures reports
and the detector folds), ``error`` maps to ``failed``, ``xfailed`` to ``skipped`` (an
expected-red reproduction says nothing about flakiness) and ``not_run`` rows are left
out (no execution happened). The JSONL history file stays as it is; retiring it is a
separate change.
"""

from __future__ import annotations

from typing import Any

from autonomous_ui.flakiness.models import FlakRecord

_FINAL = {
    "passed": "passed",
    "failed": "failed",
    "error": "failed",
    "skipped": "skipped",
    "xfailed": "skipped",
}


def history_from_store(store: Any, window: int = 30) -> list[FlakRecord]:
    """Attempts of the last ``window`` runs as FlakRecords, in execution order."""
    out: list[FlakRecord] = []
    for row in store.query("window_attempts", window=window):
        if row["outcome"] == "not_run":
            continue
        final = row["attempt"] == row["final_attempt"]
        outcome = _FINAL.get(row["outcome"], "failed") if final else "rerun"
        out.append(
            FlakRecord(
                test_id=row["nodeid"],
                run_id=row["run_id"],
                outcome=outcome,
                duration_s=(row["duration_ms"] or 0) / 1000.0,
                error=(row["message"] or "") if outcome in ("failed", "rerun") else "",
                timestamp=row["started_at"] or row["run_started_at"] or "",
                worker=row["worker"] or "main",
                environment=row["environment"] or "",
            )
        )
    return out


def grouped_history(store: Any, window: int = 30) -> dict[str, list[FlakRecord]]:
    """``history_from_store`` grouped per test, ready for ``FlakinessDetector.get_profiles``."""
    groups: dict[str, list[FlakRecord]] = {}
    for rec in history_from_store(store, window):
        groups.setdefault(rec.test_id, []).append(rec)
    return groups


__all__ = ["grouped_history", "history_from_store"]
