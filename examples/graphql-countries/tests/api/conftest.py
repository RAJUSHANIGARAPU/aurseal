"""Live fixtures: one client per session, pointed at the endpoint under test."""

from __future__ import annotations

import pytest

from graphql_countries.client import GraphQLClient
from graphql_countries.contract import load_snapshot
from graphql_countries.services import CountriesService
from graphql_countries.settings import Settings

# A free public API: say who is calling.
HEADERS = {"User-Agent": "countries-suite-example"}


@pytest.fixture(scope="session")
def api(settings: Settings) -> GraphQLClient:
    """No local schema: these tests send invalid operations on purpose."""
    return GraphQLClient(settings.api_base_url, timeout_s=settings.timeout_s, headers=HEADERS)


@pytest.fixture(scope="session")
def countries(settings: Settings) -> CountriesService:
    """Validates every operation against schema.graphql before it is sent."""
    client = GraphQLClient(
        settings.api_base_url, timeout_s=settings.timeout_s, headers=HEADERS, schema=load_snapshot()
    )
    return CountriesService(client)
