"""The service on httpx.MockTransport: answers become models, drift is refused."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from graphql_countries.client import GraphQLClient
from graphql_countries.contract import load_snapshot
from graphql_countries.services import CountriesService

pytestmark = pytest.mark.offline

NETHERLANDS = {
    "code": "NL",
    "name": "Netherlands",
    "capital": "Amsterdam",
    "currency": "EUR",
    "continent": {"code": "EU", "name": "Europe"},
    "languages": [{"code": "nl", "name": "Dutch"}],
}


def service_answering(
    data: object, sent: list[dict[str, object]] | None = None
) -> CountriesService:
    def handle(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(json.loads(request.content))
        return httpx.Response(200, json={"data": data})

    client = GraphQLClient(
        "https://countries.example.test",
        transport=httpx.MockTransport(handle),
        schema=load_snapshot(),
    )
    return CountriesService(client)


def test_a_country_is_parsed_into_the_model():
    country = service_answering({"country": NETHERLANDS}).by_code("NL")

    assert country is not None
    assert country.capital == "Amsterdam"
    assert [language.name for language in country.languages] == ["Dutch"]


def test_the_code_is_sent_as_a_variable_not_spliced_into_the_query():
    sent: list[dict[str, object]] = []
    service_answering({"country": None}, sent).by_code("NL")

    assert sent[0]["variables"] == {"code": "NL"}
    assert "NL" not in str(sent[0]["query"])


def test_null_is_none():
    assert service_answering({"country": None}).by_code("XX") is None


def test_a_country_without_capital_or_currency_is_valid():
    """Antarctica has neither; the schema says both are nullable."""
    country = service_answering({"country": {**NETHERLANDS, "capital": None, "currency": None}})
    parsed = country.by_code("AQ")
    assert parsed is not None
    assert (parsed.capital, parsed.currency) == (None, None)


@pytest.mark.parametrize(
    "drift",
    [
        pytest.param({"code": "NLD"}, id="code-is-not-alpha-2"),
        pytest.param({"name": ""}, id="name-became-empty"),
        pytest.param({"languages": None}, id="languages-became-null"),
        pytest.param({"continent": "EU"}, id="continent-lost-its-fields"),
        pytest.param({"population": 17_900_000}, id="a-field-nobody-selected"),
    ],
)
def test_an_answer_that_drifts_from_the_model_is_rejected(drift):
    with pytest.raises(ValidationError):
        service_answering({"country": {**NETHERLANDS, **drift}}).by_code("NL")


def test_a_continent_listing_is_parsed():
    rows = [{"code": "NL", "name": "Netherlands", "continent": {"code": "EU"}}]
    assert [c.code for c in service_answering({"countries": rows}).in_continent("EU")] == ["NL"]
