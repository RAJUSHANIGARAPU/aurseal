"""'Today' and calendar days in the service's business timezone, not the runner's.

A service decides which day an instant belongs to in its own zone. A runner
in UTC is a day behind that service between local midnight and the zone's
offset: a fixture seeded with the runner's "today" during that window starts
yesterday as far as the service is concerned, and a stored instant read back
with the runner's calendar lands on the wrong day.

The zone is the config key ``business_timezone`` (environment variable
``BUSINESS_TIMEZONE``, then ``.env.<env>``, then ``config/environments.json``),
``UTC`` when unset. :func:`today` and :func:`local_date` use it; both take an
explicit ``zone`` and :func:`today` a ``now`` callable so a test can pin the
clock inside the risky window.

An instant without an offset is refused: guessing its zone is exactly the bug
this module exists to prevent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONFIG_KEY = "business_timezone"
DEFAULT_ZONE = "UTC"


class NaiveInstant(ValueError):
    """An instant without a UTC offset; its calendar day is unknowable."""


def zone_named(name: str) -> tzinfo:
    """The ``tzinfo`` for an IANA name; raises ``ValueError`` for an unknown one."""
    text = (name or "").strip() or DEFAULT_ZONE
    if text.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{CONFIG_KEY}={name!r} is not a known IANA timezone") from exc


def business_zone(config=None) -> tzinfo:
    """The configured business timezone (``UTC`` when the key is unset)."""
    if config is None:
        from core.config_reader import ConfigReader

        config = ConfigReader()
    return zone_named(str(config.get(CONFIG_KEY, DEFAULT_ZONE)))


def _aware(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise NaiveInstant(
            f"instant {instant!r} has no offset; refusing to guess which day it belongs to"
        )
    return instant


def local_date(instant: datetime, *, zone: tzinfo | None = None) -> date:
    """The calendar day ``instant`` falls on in the business zone."""
    return _aware(instant).astimezone(zone or business_zone()).date()


def today(*, zone: tzinfo | None = None, now: Callable[[], datetime] | None = None) -> date:
    """Today in the business zone. ``now`` must return an offset-aware instant."""
    moment = now() if now is not None else datetime.now(timezone.utc)
    return local_date(moment, zone=zone)
