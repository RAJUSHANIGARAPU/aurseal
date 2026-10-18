"""A booking as the API sends and receives it."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BookingDates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkin: dt.date
    checkout: dt.date

    @model_validator(mode="after")
    def _checkout_not_before_checkin(self) -> BookingDates:
        if self.checkout < self.checkin:
            raise ValueError("checkout is before checkin")
        return self


class Booking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firstname: str = Field(min_length=1)
    lastname: str = Field(min_length=1)
    totalprice: int = Field(ge=0)
    depositpaid: bool
    bookingdates: BookingDates
    additionalneeds: str | None = None


class CreatedBooking(BaseModel):
    bookingid: int
    booking: Booking
