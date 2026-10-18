"""Booking CRUD. Each test creates the bookings it reads and deletes them afterwards, so it
never depends on, or disturbs, the rows other users of the shared API create."""

from __future__ import annotations

import pytest

from api_rest_restful_booker.client import ApiClient
from api_rest_restful_booker.data.bookings import a_booking
from api_rest_restful_booker.services import BookingsService

pytestmark = pytest.mark.regression


@pytest.mark.smoke
def test_create_booking_returns_id_and_echoes_payload(bookings: BookingsService, created_ids):
    sent = a_booking()

    created = bookings.create(sent)
    created_ids.append(created.bookingid)

    assert created.bookingid > 0
    assert created.booking == sent


def test_get_returns_the_booking_as_created(bookings: BookingsService, new_booking):
    created = new_booking()
    assert bookings.get(created.bookingid) == created.booking


def test_update_replaces_every_field(bookings: BookingsService, new_booking):
    created = new_booking()
    replacement = a_booking(totalprice=999, depositpaid=False, additionalneeds="Late checkout")

    assert bookings.update(created.bookingid, replacement) == replacement
    assert bookings.get(created.bookingid) == replacement


@pytest.mark.parametrize(
    "fields",
    [{"lastname": "Changed"}, {"totalprice": 1}, {"depositpaid": False, "additionalneeds": "None"}],
    ids=["lastname", "totalprice", "two-fields"],
)
def test_partial_update_changes_only_supplied_fields(
    bookings: BookingsService, new_booking, fields
):
    created = new_booking()

    updated = bookings.partial_update(created.bookingid, fields)

    assert updated.model_dump() == {**created.booking.model_dump(), **fields}


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_update_booking_without_token_is_rejected_403(api: ApiClient, new_booking, method):
    created = new_booking()

    response = api.request(
        method, f"/booking/{created.bookingid}", json=created.booking.model_dump(mode="json")
    )

    assert response.status_code == 403


def test_list_bookings_filtered_by_name_contains_created_booking(
    bookings: BookingsService, new_booking
):
    created = new_booking()
    ids = bookings.ids(firstname=created.booking.firstname, lastname=created.booking.lastname)
    assert ids == [created.bookingid]


def test_deleted_booking_is_gone(bookings: BookingsService, api: ApiClient):
    created = bookings.create(a_booking())

    bookings.delete(created.bookingid)

    assert api.get(f"/booking/{created.bookingid}").status_code == 404


@pytest.mark.xfail(reason="FINDING-1: malformed dates are stored as '0NaN-aN-aN'", strict=True)
def test_malformed_dates_are_rejected(api: ApiClient, created_ids):
    payload = a_booking().model_dump(mode="json")
    payload["bookingdates"] = {"checkin": "not-a-date", "checkout": "2027-13-45"}

    response = api.post("/booking", json=payload)
    if response.status_code == 200:
        created_ids.append(response.json()["bookingid"])

    assert response.status_code == 400


@pytest.mark.xfail(reason="FINDING-2: a booking without required fields is a 500", strict=True)
def test_missing_required_fields_are_a_client_error(api: ApiClient):
    response = api.post("/booking", json={"firstname": "Incomplete"})
    assert response.status_code == 400
