"""Load ``case -> tracker id`` mappings from a two-column CSV into ``case_links``.

CSV: a header row, then ``case_id,tracker_id`` (or ``case_key,tracker_id``: a key is
turned into its derived case id). Rows are stored as ``case_links(kind='tms:<name>')``
with source ``import``; loading the same file twice adds nothing.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from results.catalogue import case_id_for_key


def load_mapping(csv_path: str | Path, kind: str, *, store: Any = None) -> int:
    """Store the mapping; returns how many links were new. ``kind`` is ``tms:<tracker>``."""
    if not kind.startswith("tms:"):
        raise ValueError("a tracker mapping kind must look like 'tms:<tracker>'")
    owned = store is None
    if owned:
        from results.store import open_store

        store = open_store()
    try:
        links = []
        with Path(csv_path).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            by_key = bool(header) and header[0].strip().lower() == "case_key"
            for row in reader:
                if len(row) < 2 or not row[0].strip() or not row[1].strip():
                    continue
                case = case_id_for_key(row[0].strip()) if by_key else row[0].strip()
                links.append((case, kind, row[1].strip()))
        known = {r[0] for r in store.conn.execute("SELECT case_id FROM cases")}
        missing = [c for c, _, _ in links if c not in known]
        if missing:
            raise ValueError(f"unknown case id(s) in the mapping: {', '.join(missing[:5])}")
        return store.add_links(links, source="import")
    finally:
        if owned:
            store.close()


__all__ = ["load_mapping"]
