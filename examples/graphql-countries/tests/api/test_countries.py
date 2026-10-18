"""Country lookups on the live API: stable facts, the null path, filters and request errors."""

from __future__ import annotations

import pytest

from graphql_countries.client import GraphQLClient, GraphQLResponseError
from graphql_countries.data.countries import FACTS, CountryFact
from graphql_countries.services import CountriesService

pytestmark = pytest.mark.regression


@pytest.mark.smoke
def test_country_by_code_returns_currency_and_languages(countries: CountriesService):
    netherlands = countries.by_code("NL")

    assert netherlands is not None
    assert (netherlands.name, netherlands.capital) == ("Netherlands", "Amsterdam")
    assert netherlands.currency == "EUR"
    assert "Dutch" in {language.name for language in netherlands.languages}


@pytest.mark.parametrize("fact", FACTS, ids=lambda fact: fact.code)
def test_country_matches_the_known_facts(countries: CountriesService, fact: CountryFact):
    country = countries.by_code(fact.code)

    assert country is not None
    assert (country.name, country.capital, country.currency) == (
        fact.name,
        fact.capital,
        fact.currency,
    )
    assert country.continent.code == fact.continent
    assert fact.language in {language.name for language in country.languages}


@pytest.mark.parametrize("code", ["XX", "ZZ", "NLD"], ids=["unassigned", "reserved", "alpha-3"])
def test_unknown_country_code_returns_null_not_error(countries: CountriesService, code: str):
    assert countries.by_code(code) is None


def test_countries_filtered_by_continent_all_belong_to_it(countries: CountriesService):
    europe = countries.in_continent("EU")

    assert {country.continent.code for country in europe} == {"EU"}
    assert {"NL", "DE", "FR"} <= {country.code for country in europe}
    assert "JP" not in {country.code for country in europe}


def test_a_filter_matching_nothing_is_an_empty_list(countries: CountriesService):
    assert countries.in_continent("XX") == []


def test_a_missing_required_variable_is_rejected(api: GraphQLClient):
    with pytest.raises(GraphQLResponseError) as raised:
        api.execute("query CountryByCode($code: ID!) { country(code: $code) { name } }")
    assert any("$code" in message for message in raised.value.messages)


def test_a_wrongly_typed_argument_is_rejected(api: GraphQLClient):
    with pytest.raises(GraphQLResponseError):
        api.execute("{ countries(filter: { continent: { eq: 7 } }) { code } }")


def test_country_codes_are_case_sensitive(countries: CountriesService):
    """Finding 1: a lower-case code answers null, indistinguishable from an unknown country."""
    assert countries.by_code("nl") is None
