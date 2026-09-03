"""Append-only JSONL store for test execution history.

Each line in the file is one FlakRecord serialised as JSON.
Append-only design means concurrent writers are safe (OS-level file append
is atomic for small writes on all major platforms), and the store never
needs to be rewritten for basic operations.

Bounded, because append-only was the whole design and nothing ever removed a
line. Left alone the file grew for as long as the checkout lived: in the source
repo it reached 5.4 MB / 18,486 records, and every ``pytest_sessionfinish``
parsed the whole thing — twice — to decide whether anything was flaky. The old
records were not even useful: a flakiness verdict is about the code as it is
now, and a year-old failure from a page object that has since been rewritten
only drags the rate around.

``prune()`` keeps the newest ``max_records`` and drops the rest. It is called at
session end, and also from ``record()`` every ``prune_every`` appends so that a
run which never reaches session end — a crash, ``-x``, Ctrl-C — still cannot
grow the file without limit.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from autonomous_ui.flakiness.models import FlakRecord

_DEFAULT_PATH = Path("reports/flakiness/history.jsonl")

# ~5k records is more history than any verdict here uses: MIN_RUNS is 5, and
# load_for_test() already caps a single test at its newest 200.
_DEFAULT_MAX_RECORDS = 5000
# Rewriting the file is O(n), so it is not done per append. 500 is well under
# the cap, so the file can never overshoot it by more than one suite's worth.
_DEFAULT_PRUNE_EVERY = 500


class HistoryStore:
    """Persists and reads test run records from a bounded append-only JSONL file."""

    def __init__(
        self,
        store_path: Path | None = None,
        max_records: int = _DEFAULT_MAX_RECORDS,
        prune_every: int = _DEFAULT_PRUNE_EVERY,
    ) -> None:
        self._path = store_path or _DEFAULT_PATH
        self._max_records = max_records
        self._prune_every = prune_every
        self._appends_since_prune = 0

    def record(self, rec: FlakRecord) -> None:
        """Append one test result. Creates the file and parent dirs if missing."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict()) + "\n")
        except OSError:
            return  # non-fatal — flakiness tracking must not break the test run

        self._appends_since_prune += 1
        if self._appends_since_prune >= self._prune_every:
            self.prune()

    def prune(self) -> None:
        """Drop all but the newest ``max_records`` lines. Never raises."""
        self._appends_since_prune = 0
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            kept = [line for line in lines if line.strip()]
            if len(kept) <= self._max_records:
                return
            # Written to a sibling and swapped in, so a reader (another xdist
            # worker, or a crash mid-write) never sees a half-rewritten history.
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text("\n".join(kept[-self._max_records :]) + "\n", encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass  # non-fatal, same reason as record()

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
