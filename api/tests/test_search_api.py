import json

import pytest

from api.clients.search_client import SearchClient


@pytest.mark.api
def test_plain_http_search_is_refused_by_the_edge(config):
    """
    The search endpoint is not reachable from a plain HTTP client.

    Catawiki's edge refuses requests that do not come from a real browser, so
    the API-level path returns 403 and the browser suite is the only way to
    exercise search. That is a property of the target site, not a defect here,
    and it is worth pinning: if it ever changes, an API-level suite becomes
    possible and this test says so by failing.

    This test previously read ``test_search_api_returns_200_and_html`` while
    asserting the opposite, and it passed against a malformed URL
    (``/en//en/s``) because a refusal looks identical whatever you ask for. The
    URL is now asserted separately and offline in
    ``tests/api/test_search_client.py``; here it is checked before the call so
    a 403 cannot stand in for a request that was never well-formed.
    """
    with open("ui/testdata/test_data.json") as f:
        keyword = json.load(f)["valid_search"]

    client = SearchClient(config.get_base_url())

    # Precondition, not decoration: without it the assertion below passes for
    # any URL at all, which is exactly how the broken one survived.
    assert client.search_url(keyword).startswith("https://www.catawiki.com/en/s?")

    response = client.search(keyword)

    assert response.status_code in (401, 403), (
        f"expected the edge to refuse a plain HTTP client, got {response.status_code}. "
        "If this is now allowed, the API-level search suite can be written for real."
    )
