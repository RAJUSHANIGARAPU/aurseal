"""The settlements party filter narrows locally and queries only when a party is chosen."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from ui.pages.demo.admin import SettlementsScreen

pytestmark = pytest.mark.demo_ui


def test_party_filter_queries_only_on_selection(demo_page, demo_sut, demo_control, demo_seed):
    partner = demo_seed.partner()
    screen = SettlementsScreen(demo_page, demo_sut.base_url).open()
    try:
        assert screen.list_requests == []
        fragment = partner["name"][-4:]
        assert screen.type_party(fragment).endswith(fragment), "control: the input lost the text"
        demo_page.wait_for_timeout(300)  # give a would-be debounced request time to leave
        narrowed = screen.options()
        assert narrowed and all(fragment.lower() in o.lower() for o in narrowed), narrowed
        assert screen.list_requests == [], f"typing sent requests: {screen.list_requests}"

        screen.choose_option(partner["number"])
        assert len(screen.list_requests) == 1, screen.list_requests
        query = parse_qs(urlparse(screen.list_requests[0]).query)
        assert query == {"party": [str(partner["id"])], "tab": ["open"]}

        screen.open_tab("Closed")
        assert len(screen.list_requests) == 2
        query = parse_qs(urlparse(screen.list_requests[1]).query)
        assert query == {"party": [str(partner["id"])], "tab": ["closed"]}
    finally:
        screen.close()
