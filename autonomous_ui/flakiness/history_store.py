"""Append-only JSONL store for test execution history.

Each line in the file is one FlakRecord serialised as JSON.
Append-only design means concurrent writers are safe (OS-level file append
is atomic for small writes on all major platforms), and the store never
needs to be rewritten for basic operations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from autonomous_ui.flakiness.models import FlakRecord

_DEFAULT_PATH = Path("reports/flakiness/history.jsonl")


class HistoryStore:
    """Persists and reads test run records from an append-only JSONL file."""

    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path or _DEFAULT_PATH

    def record(self, rec: FlakRecord) -> None:
        """Append one test result. Creates the file and parent dirs if missing."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict()) + "\n")
        except OSError:
            pass  # non-fatal — flakiness tracking must not break the test run

    def load_all(self) -> list[FlakRecord]:
        """Return every record in chronological order."""
        if not self._path.exists():
            return []
        records: list[FlakRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(FlakRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError):
                pass
        return records

    def load_for_test(self, test_id: str, limit: int = 200) -> list[FlakRecord]:
        """Return the most recent *limit* records for *test_id*."""
        all_records = [r for r in self.load_all() if r.test_id == test_id]
        return all_records[-limit:]

    def grouped_by_test(self) -> dict[str, list[FlakRecord]]:
        """Return all records grouped by test_id, preserving insertion order."""
        groups: dict[str, list[FlakRecord]] = defaultdict(list)
        for rec in self.load_all():
            groups[rec.test_id].append(rec)
        return dict(groups)

    def clear(self) -> None:
        """Delete the history file. Intended for tests only."""
        if self._path.exists():
            self._path.unlink()
