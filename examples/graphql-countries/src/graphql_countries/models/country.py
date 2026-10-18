"""What ``CountryFields`` and the continent listing select."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Selection(BaseModel):
    # A field nobody selected, or a changed type, fails here instead of deep in a test.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Continent(_Selection):
    code: str = Field(pattern=r"^[A-Z]{2}$")
    name: str


class Language(_Selection):
    code: str
    name: str


class Country(_Selection):
    code: str = Field(pattern=r"^[A-Z]{2}$")
    name: str = Field(min_length=1)
    capital: str | None
    currency: str | None
    continent: Continent
    languages: list[Language]


class ContinentCode(_Selection):
    code: str


class CountrySummary(_Selection):
    code: str = Field(pattern=r"^[A-Z]{2}$")
    name: str = Field(min_length=1)
    continent: ContinentCode
