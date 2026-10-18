"""Live fixtures: one client and one admin token per session; every booking is cleaned up."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from api_rest_restful_booker.client import ApiClient
from api_rest_restful_booker.data.bookings import a_booking
from api_rest_restful_booker.models import CreatedBooking
from api_rest_restful_booker.services import AuthService, BookingsService
from api_rest_restful_booker.settings import Settings


@pytest.fixture(scope="session")
def api(settings: Settings) -> Iterator[ApiClient]:
    with ApiClient(
        settings.api_base_url,
        timeout_s=settings.timeout_s,
        max_retries=settings.max_retries,
        headers={"Accept": "application/json", "User-Agent": "booking-suite-example"},
    ) as client:
        yield client


@pytest.fixture(scope="session")
def token(api: ApiClient, settings: Settings) -> str:
    return AuthService(api).token(
        settings.admin_username, settings.admin_password.get_secret_value()
    )


@pytest.fixture
def bookings(api: ApiClient, token: str) -> BookingsService:
    return BookingsService(api, token)


@pytest.fixture
def created_ids(bookings: BookingsService) -> Iterator[list[int]]:
    """Ids a test created; each is deleted afterwards, whatever the test's outcome."""
    ids: list[int] = []
    yield ids
    for booking_id in ids:
        bookings.delete(booking_id)


@pytest.fixture
def new_booking(bookings: BookingsService, created_ids: list[int]) -> Callable[..., CreatedBooking]:
    def create(**overrides: object) -> CreatedBooking:
        created = bookings.create(a_booking(**overrides))
        created_ids.append(created.bookingid)
        return created

    return create
