"""Facts about countries that do not change between releases of the API.

Kept to stable, verifiable values (capital, currency, an official language); population,
names in other languages and similar data that does change are deliberately left out.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CountryFact:
    code: str
    name: str
    capital: str
    currency: str
    continent: str
    language: str


FACTS: tuple[CountryFact, ...] = (
    CountryFact("NL", "Netherlands", "Amsterdam", "EUR", "EU", "Dutch"),
    CountryFact("JP", "Japan", "Tokyo", "JPY", "AS", "Japanese"),
    CountryFact("BR", "Brazil", "Brasília", "BRL", "SA", "Portuguese"),
    CountryFact("KE", "Kenya", "Nairobi", "KES", "AF", "Swahili"),
)
