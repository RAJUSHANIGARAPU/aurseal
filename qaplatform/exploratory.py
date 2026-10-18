"""Exploratory session capture: charter, timestamped notes, debrief; rules only.

Session-based exploratory testing: a **charter** (mission, area, linked requirements, an
optional timebox), a stream of typed **notes** (``observation``, ``question``, ``bug``,
``idea``) with optional attachments and defect keys, and a **debrief**. A session is a run
of source ``exploratory``. What it contributes to traceability is ``recorded`` for each
linked requirement, **never** ``proved``: a session is evidence that someone looked, not
that a requirement holds. Going over the timebox is flagged, not refused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

NOTE_KINDS = ("observation", "question", "bug", "idea")
SESSION_PREFIX = "session::"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def duration_min(started_at: str, ended_at: str | None, now: datetime) -> float:
    end = _parse(ended_at) if ended_at else now
    return round((end - _parse(started_at)).total_seconds() / 60, 1)


def summary(
    session: Mapping[str, Any], notes: Sequence[Mapping[str, Any]], now: datetime
) -> dict[str, Any]:
    minutes = duration_min(session["started_at"], session.get("ended_at"), now)
    box = session.get("timebox_min")
    counts = {k: 0 for k in NOTE_KINDS}
    for n in notes:
        counts[n["kind"]] += 1
    return {
        "id": session["id"],
        "run_id": session["run_id"],
        "test_id": SESSION_PREFIX + session["id"],
        "charter": session["charter"],
        "area": session.get("area") or "",
        "requirements": list(session.get("requirements") or []),
        "status": session["status"],
        "tester": session["tester_user_id"],
        "started_at": session["started_at"],
        "ended_at": session.get("ended_at"),
        "timebox_min": box,
        "duration_min": minutes,
        "over_timebox": bool(box) and minutes > box,
        "notes": [dict(n) for n in notes],
        "note_counts": counts,
        "bugs": [
            {"seq": n["seq"], "text": n["text"], "defect_key": n.get("defect_key")}
            for n in notes
            if n["kind"] == "bug"
        ],
        "debrief": session.get("debrief"),
        "contribution": "recorded",
    }


__all__ = ["NOTE_KINDS", "SESSION_PREFIX", "duration_min", "summary"]
