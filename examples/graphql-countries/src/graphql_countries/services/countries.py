"""Countries, through the operations in ``operations/``."""

from __future__ import annotations

from .. import operations
from ..client import GraphQLClient
from ..models import Country, CountrySummary


class CountriesService:
    def __init__(self, client: GraphQLClient) -> None:
        self.client = client

    def by_code(self, code: str) -> Country | None:
        """The country, or ``None`` when the API answers ``country: null``."""
        data = self.client.execute(operations.document("country_by_code"), {"code": code})
        return None if data["country"] is None else Country.model_validate(data["country"])

    def in_continent(self, continent: str) -> list[CountrySummary]:
        data = self.client.execute(
            operations.document("countries_in_continent"), {"continent": continent}
        )
        return [CountrySummary.model_validate(row) for row in data["countries"]]
