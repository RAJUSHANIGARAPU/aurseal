"""
Offline checks on the URL ``SearchClient`` builds.

These exist because the only test that touched this client asserted a 403 from
the live site. The site returns 403 to any plain HTTP client regardless of
path, so the assertion held while the client was requesting
``https://www.catawiki.com/en//en/s`` — a duplicated locale segment that no
test could see. URL construction does not need the network to be checked, and
checking it here means a wrong URL fails on its own terms instead of hiding
behind someone else's bot protection.
"""

from __future__ import annotations

import pytest

from api.clients.search_client import SearchClient

BASE = "https://www.catawiki.com/en/"


class TestSearchUrl:
    def test_does_not_duplicate_the_locale_segment(self):
        url = SearchClient(BASE).search_url("rolex")
        assert "/en//en/" not in url
        assert url.startswith("https://www.catawiki.com/en/s?")

    def test_has_no_empty_path_segment(self):
        """``//`` anywhere after the scheme means two joins collided."""
        url = SearchClient(BASE).search_url("rolex")
        assert "//" not in url.split("://", 1)[1]

    @pytest.mark.parametrize("base", [BASE, BASE.rstrip("/")])
    def test_trailing_slash_on_the_base_makes_no_difference(self, base):
        assert SearchClient(base).search_url("rolex") == (
            "https://www.catawiki.com/en/s?q=rolex"
        )

    def test_keyword_is_query_encoded(self):
        url = SearchClient(BASE).search_url("rolex watch")
        assert url.endswith("?q=rolex+watch")

    def test_matches_the_url_the_ui_test_expects(self):
        """
        The browser test asserts the address bar reads
        ``https://www.catawiki.com/en/s?q=<keyword>`` after a search. The API
        client should be aimed at the same endpoint; when the two drifted apart
        nothing noticed.
        """
        assert SearchClient(BASE).search_url("rolex") == (
            "https://www.catawiki.com/en/s?q=rolex"
        )
