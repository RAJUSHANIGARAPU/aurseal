"""The booking service against recorded answers on httpx.MockTransport: no network."""

from __future__ import annotations

import copy
from typing import Any

import httpx
import jsonschema
import pytest

from api_rest_restful_booker.client import ApiClient
from api_rest_restful_booker.models import Booking
from api_rest_restful_booker.services import AuthenticationFailed, AuthService, BookingsService

pytestmark = pytest.mark.offline

# Shapes recorded from the live API's documented examples.
BOOKING: dict[str, Any] = {
    "firstname": "Jim",
    "lastname": "Brown",
    "totalprice": 111,
    "depositpaid": True,
    "bookingdates": {"checkin": "2027-01-01", "checkout": "2027-01-04"},
    "additionalneeds": "Breakfast",
}
CREATED = {"bookingid": 1, "booking": BOOKING}


def client_answering(payload: object, status: int = 200) -> ApiClient:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return ApiClient("https://api.example.test", transport=httpx.MockTransport(handle))


def drifted(path: tuple[str, ...], value: object) -> dict[str, Any]:
    booking = copy.deepcopy(BOOKING)
    target: dict[str, Any] = booking
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return booking


def test_a_recorded_booking_parses():
    booking = BookingsService(client_answering(BOOKING)).get(1)
    assert booking.bookingdates.checkout.isoformat() == "2027-01-04"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("totalprice",), "111"),
        (("depositpaid",), "true"),
        (("firstname",), ""),
        (("bookingdates", "checkin"), "0NaN-aN-aN"),
    ],
    ids=["price-as-string", "deposit-as-string", "empty-name", "malformed-date"],
)
def test_a_booking_that_drifts_from_the_contract_is_rejected(path, value):
    with pytest.raises(jsonschema.ValidationError):
        BookingsService(client_answering(drifted(path, value))).get(1)


def test_a_create_answer_without_an_id_is_rejected():
    service = BookingsService(client_answering({"booking": BOOKING}))
    with pytest.raises(jsonschema.ValidationError, match="bookingid"):
        service.create(Booking.model_validate(BOOKING))


def test_bad_credentials_raise_instead_of_returning_an_empty_token():
    with pytest.raises(AuthenticationFailed, match="Bad credentials"):
        AuthService(client_answering({"reason": "Bad credentials"})).token("admin", "wrong")
