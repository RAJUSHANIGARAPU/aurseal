"""
One structured line per healing decision, applied or not.

A heal log that records only the heals that landed cannot answer the questions
it exists for: how often did the healer try and refuse, which keys keep coming
back, which proposals are waiting for review. ``HealEventLog`` appends one JSON
object per ``HealingResult`` — strategy, whether it was applied, whether the
registry was written or a proposal was filed, the locator key with its old and
new entry, and the reason — to ``reports/healing/healing-events.jsonl``.

It is written by ``UIHealer`` itself, so every caller (the orchestrator, an
agent, a script) is covered, and it is bounded like the flakiness history: every
``prune_every`` appends it keeps only the newest ``max_events`` lines, rewritten
through a sibling file and ``os.replace``. Logging never fails a heal.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.logger import logger

DEFAULT_PATH = Path("reports/healing/healing-events.jsonl")
PATH_VARIABLE = "HEAL_EVENTS_FILE"
DEFAULT_MAX_EVENTS = 5000
DEFAULT_PRUNE_EVERY = 100


@dataclass(frozen=True)
class HealEvent:
    test: str
    strategy: str
    applied: bool
    details: str
    failure_type: str = ""
    confidence: str = ""
    locator_key: str = ""
    old_locator: dict | None = None
    new_locator: dict | None = None
    persisted: bool | None = None  # None: not a locator write; False: proposal filed
    patched_files: list[str] = field(default_factory=list)
    timestamp: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        if not data["timestamp"]:
            data["timestamp"] = datetime.now(tz=timezone.utc).isoformat()
        return data


def default_path() -> Path:
    override = os.environ.get(PATH_VARIABLE, "").strip()
    return Path(override) if override else DEFAULT_PATH


class HealEventLog:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        prune_every: int = DEFAULT_PRUNE_EVERY,
    ) -> None:
        self.path = Path(path) if path is not None else default_path()
        self.max_events = max_events
        self.prune_every = prune_every
        self._since_prune = 0

    def append(self, event: HealEvent) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("heal event not logged: %s", exc)
            return
        self._since_prune += 1
        if self._since_prune >= self.prune_every:
            self.prune()

    def prune(self) -> None:
        """Keep the newest ``max_events`` lines. Never raises."""
        self._since_prune = 0
        try:
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(lines) <= self.max_events:
                return
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text("\n".join(lines[-self.max_events :]) + "\n", encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def read(self) -> list[dict]:
        """Every readable event, oldest first; malformed lines are skipped."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        events = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
