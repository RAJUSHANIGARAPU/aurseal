"""Structured JSON trace logging for all agent events."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TRACES_DIR = Path("reports/agent_traces")
_CONSOLE_FORMAT = "%(asctime)s | AGENT | %(message)s"

logger = logging.getLogger("api_agent")


class AgentLogger:
    """Writes structured JSON events to a per-session JSONL file.

    Also prints human-readable lines to stdout.
    """

    def __init__(self, session_id: str, traces_dir: Path | None = None) -> None:
        self.session_id = session_id
        self._dir = traces_dir or _TRACES_DIR
        os.makedirs(self._dir, exist_ok=True)
        self._file = self._dir / f"{session_id}.jsonl"
        self._setup_console_logger()

    def log(self, agent: str, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Emit one structured event.

        Args:
            agent: Short agent name (e.g. "ingestion", "orchestrator").
            event_type: Event kind (e.g. "start", "complete", "error").
            data: Arbitrary payload dict; may be None.
        """
        event = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_id": self.session_id,
            "agent": agent,
            "event_type": event_type,
            "data": data or {},
        }
        self._write_jsonl(event)
        human = f"[{agent.upper()}] {event_type}"
        if data:
            summary_parts = []
            for k, v in data.items():
                if isinstance(v, str | int | float | bool):
                    summary_parts.append(f"{k}={v}")
            if summary_parts:
                human += " | " + " ".join(summary_parts)
        logger.info(human)

    def _write_jsonl(self, event: dict) -> None:
        try:
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        except OSError as exc:
            logger.warning("Could not write trace event: %s", exc)

    @staticmethod
    def _setup_console_logger() -> None:
        root = logging.getLogger("api_agent")
        if root.hasHandlers():
            return
        root.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        root.addHandler(handler)
        root.propagate = False
