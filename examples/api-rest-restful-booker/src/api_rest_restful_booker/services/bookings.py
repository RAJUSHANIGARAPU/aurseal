"""The ``/booking`` resource. Writes need the token from ``AuthService``."""

from __future__ import annotations

from typing import Any

import httpx

from .. import contract
from ..client import ApiClient
from ..models import Booking, CreatedBooking


class BookingsService:
    path = "/booking"

    def __init__(self, client: ApiClient, token: str | None = None) -> None:
        self.client = client
        self.token = token

    def create(self, booking: Booking) -> CreatedBooking:
        response = self.client.post(self.path, json=booking.model_dump(mode="json"))
        response.raise_for_status()
        payload = response.json()
        contract.check("created-booking", payload)
        contract.check("booking", payload["booking"])
        return CreatedBooking.model_validate(payload)

    def get(self, booking_id: int) -> Booking:
        return self._booking(self.client.get(f"{self.path}/{booking_id}"))

    def update(self, booking_id: int, booking: Booking) -> Booking:
        response = self.client.put(
            f"{self.path}/{booking_id}",
            json=booking.model_dump(mode="json"),
            headers=self._auth(),
        )
        return self._booking(response)

    def partial_update(self, booking_id: int, fields: dict[str, Any]) -> Booking:
        response = self.client.patch(f"{self.path}/{booking_id}", json=fields, headers=self._auth())
        return self._booking(response)

    def delete(self, booking_id: int) -> None:
        # The API answers a successful delete with 201 Created.
        self.client.delete(f"{self.path}/{booking_id}", headers=self._auth()).raise_for_status()

    def ids(self, **filters: str) -> list[int]:
        response = self.client.get(self.path, params=filters)
        response.raise_for_status()
        return [int(entry["bookingid"]) for entry in response.json()]

    def _auth(self) -> dict[str, str]:
        return {"Cookie": f"token={self.token}"} if self.token else {}

    @staticmethod
    def _booking(response: httpx.Response) -> Booking:
        response.raise_for_status()
        payload = response.json()
        contract.check("booking", payload)
        return Booking.model_validate(payload)
