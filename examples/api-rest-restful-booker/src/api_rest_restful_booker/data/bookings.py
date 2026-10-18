"""Bookings with a unique name, so a test finds its own rows among everyone else's."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from ..models import Booking, BookingDates


def a_booking(**overrides: Any) -> Booking:
    checkin = dt.date.today() + dt.timedelta(days=30)
    fields: dict[str, Any] = {
        "firstname": f"Kit{uuid.uuid4().hex[:10]}",
        "lastname": "Tester",
        "totalprice": 120,
        "depositpaid": True,
        "bookingdates": BookingDates(checkin=checkin, checkout=checkin + dt.timedelta(days=3)),
        "additionalneeds": "Breakfast",
    }
    return Booking(**{**fields, **overrides})
