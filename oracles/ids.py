"""What a UUID says about the generator that made it.

A UUID's version nibble identifies the scheme: v4 is random, v7 carries a
Unix-millisecond timestamp in its first 48 bits (RFC 9562). When a service
switches its id generator, the ids it writes tell which build produced a
record, and a v7 id can be checked against the time the record claims it was
generated.

All times are timezone-aware; a naive datetime is an error, never silently
read as local time.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

DEFAULT_TOLERANCE = timedelta(seconds=5)


def _parse(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{value!r} is not a UUID") from exc


def _aware(moment: datetime, name: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} {moment!r} is naive; pass a timezone-aware datetime")
    return moment


def uuid_version(value: str | uuid.UUID) -> int:
    """The version nibble (4, 7, ...). Raises ``ValueError`` for a non-RFC variant."""
    u = _parse(value)
    if u.variant != uuid.RFC_4122:
        raise ValueError(f"{value!s} is not an RFC 4122/9562 UUID (variant {u.variant})")
    return (u.int >> 76) & 0xF


def uuid7_timestamp(value: str | uuid.UUID) -> datetime:
    """The UTC instant embedded in a v7 UUID; raises for any other version."""
    version = uuid_version(value)
    if version != 7:
        raise ValueError(f"{value!s} is a v{version} UUID; only v7 embeds a timestamp")
    millis = _parse(value).int >> 80
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


def consistent_with_generation_time(
    value: str | uuid.UUID, generated_at: datetime, tolerance: timedelta = DEFAULT_TOLERANCE
) -> bool:
    """Whether a v7 id's timestamp is within ``tolerance`` of the claimed generation instant."""
    claimed = _aware(generated_at, "generated_at")
    return abs(uuid7_timestamp(value) - claimed) <= tolerance


def time_order_violations(ids_in_creation_order: Sequence[str | uuid.UUID]) -> list[str]:
    """Places where a v7 id's timestamp steps back from the previous one."""
    violations = []
    previous: tuple[str, datetime] | None = None
    for value in ids_in_creation_order:
        stamp = uuid7_timestamp(value)
        if previous is not None and stamp < previous[1]:
            violations.append(f"{value!s} ({stamp.isoformat()}) precedes {previous[0]}")
        previous = (str(value), stamp)
    return violations
