"""A tracker that is a committed YAML file: the offline default and the test double.

``defects/defects.yaml``::

    defects:
      - id: BUG-12
        title: Refund is one cent short on a 31-day month
        state: open            # open | closed
        severity: high
        labels: [billing]
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import yaml

from integrations.trackers.base import BadQuery, DefectDraft, DefectRef, DefectState

FINGERPRINT_KEY = "fingerprint"


class FileTracker:
    def __init__(self, path: Path, *, name: str = "file") -> None:
        self.path = Path(path)
        self.name = name

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        items = data.get("defects") or []
        if not isinstance(items, list):
            raise ValueError(f"{self.path}: 'defects' must be a list")
        return [dict(i) for i in items]

    def _state(self, item: dict) -> DefectState:
        state = item.get("state", "open")
        return DefectState(
            id=str(item["id"]),
            state=state if state in ("open", "closed") else "unknown",
            title=str(item.get("title") or ""),
            severity=item.get("severity"),
            labels=tuple(item.get("labels") or ()),
            url=item.get("url"),
            tracker=self.name,
        )

    def get_states(self, ids: Collection[str]) -> dict[str, DefectState]:
        known = {str(i["id"]): self._state(i) for i in self._load()}
        return {i: known.get(i, DefectState(i, "unknown", tracker=self.name)) for i in ids}

    def get_states_batch(self, ids: Collection[str]) -> dict[str, DefectState]:
        """One read for many ids. Like a tracker query language, a malformed id in the
        batch (not ``PREFIX-n``) rejects the whole query."""
        bad = [i for i in ids if not str(i).rsplit("-", 1)[-1].isdigit()]
        if bad:
            raise BadQuery(f"malformed id(s) in the query: {', '.join(sorted(bad))}")
        return self.get_states(ids)

    def find_by_fingerprint(self, fp: str) -> DefectRef | None:
        for item in self._load():
            if item.get(FINGERPRINT_KEY) == fp:
                return self._state(item)
        return None

    def create(self, draft: DefectDraft) -> DefectRef:
        items = self._load()
        prefix = next((str(i["id"]).split("-")[0] for i in items), "BUG")
        numbers = [
            int(str(i["id"]).split("-")[-1]) for i in items if str(i["id"]).split("-")[-1].isdigit()
        ]
        new_id = f"{prefix}-{max(numbers, default=0) + 1}"
        items.append(
            {
                "id": new_id,
                "title": draft.title,
                "state": "open",
                "labels": list(draft.labels),
                FINGERPRINT_KEY: draft.fingerprint,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump({"defects": items}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return self._state(items[-1])

    def list_open(self, labels: Collection[str] = ()) -> list[DefectRef]:
        wanted = set(labels)
        out = [self._state(i) for i in self._load()]
        return [d for d in out if d.state == "open" and (not wanted or wanted & set(d.labels))]
