"""Negative input matrix, generated from the model so a new field is covered automatically."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from api_rest_restful_booker.data.bookings import a_booking
from api_rest_restful_booker.models import Booking

pytestmark = pytest.mark.offline

VALID: dict[str, Any] = a_booking().model_dump(mode="json")
REQUIRED = [name for name, field in Booking.model_fields.items() if field.is_required()]
WRONG_TYPE: dict[str, Any] = {
    "firstname": 123,
    "lastname": ["Tester"],
    "totalprice": "a lot",
    "depositpaid": "maybe",
    "bookingdates": "next week",
    "additionalneeds": 42,
}


def test_every_field_has_a_wrong_type_case():
    assert set(WRONG_TYPE) == set(Booking.model_fields)


@pytest.mark.parametrize("field", REQUIRED)
def test_a_missing_required_field_is_rejected(field):
    payload = {key: value for key, value in VALID.items() if key != field}
    with pytest.raises(ValidationError, match=field):
        Booking.model_validate(payload)


@pytest.mark.parametrize("field", list(WRONG_TYPE))
def test_a_value_of_the_wrong_type_is_rejected(field):
    with pytest.raises(ValidationError, match=field):
        Booking.model_validate({**VALID, field: WRONG_TYPE[field]})


def test_checkout_before_checkin_is_rejected():
    today = dt.date.today().isoformat()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError, match="checkout is before checkin"):
        Booking.model_validate({**VALID, "bookingdates": {"checkin": today, "checkout": yesterday}})


def test_a_negative_price_is_rejected():
    with pytest.raises(ValidationError, match="totalprice"):
        Booking.model_validate({**VALID, "totalprice": -1})
