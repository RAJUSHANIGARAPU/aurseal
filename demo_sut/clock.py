"""A settable clock. Every date the demo writes comes from here, never from ``datetime.now``.

The clock either follows real time (optionally shifted by an offset) or is frozen at a
fixed instant. Tests freeze it to make periods, refunds and renewals deterministic.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc


def parse_instant(value: str) -> datetime:
    """Parse an ISO date or date-time; a bare date means midnight UTC, naive means UTC."""
    text = value.strip()
    if len(text) == 10:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalize_instant(value: str) -> str:
    """An instant as the demo stores it (``YYYY-MM-DDTHH:MM:SS.mmmZ``), so strings compare."""
    moment = parse_instant(value).astimezone(UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DemoClock:
    """Real time shifted by an offset, or a frozen instant. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frozen: datetime | None = None
        self._offset = timedelta(0)

    def now(self) -> datetime:
        with self._lock:
            if self._frozen is not None:
                return self._frozen
            return datetime.now(UTC) + self._offset

    def today(self) -> date:
        return self.now().date()

    def freeze(self, instant: str | datetime) -> None:
        moment = parse_instant(instant) if isinstance(instant, str) else instant
        with self._lock:
            self._frozen = moment.astimezone(UTC)
            self._offset = timedelta(0)

    def shift(self, days: float = 0, seconds: float = 0) -> None:
        """Move the clock; a frozen clock stays frozen at the shifted instant."""
        delta = timedelta(days=days, seconds=seconds)
        with self._lock:
            if self._frozen is not None:
                self._frozen += delta
            else:
                self._offset += delta

    def reset(self) -> None:
        with self._lock:
            self._frozen = None
            self._offset = timedelta(0)

    def describe(self) -> dict[str, object]:
        with self._lock:
            frozen = self._frozen
            offset = self._offset
        return {
            "now": self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "mode": "frozen" if frozen is not None else "real",
            "offset_seconds": offset.total_seconds(),
        }

    def header(self) -> str:
        """Compact value for the ``X-Demo-Clock`` response header."""
        state = self.describe()
        return f"{state['now']}; {state['mode']}"
