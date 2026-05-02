import json
import re

import pytest

from ui.pages.home_page import HomePage


@pytest.mark.contract
def test_search_triggers_correct_network_call(page, config):
    with open("ui/testdata/test_data.json") as f:
        data = json.load(f)

    keyword = data["valid_search"]

    home = HomePage(page, config)
    home.navigate()
    home.accept_cookies()

    search_input = page.get_by_test_id("search-field").first

    with page.expect_response(re.compile(rf"/s\?q={re.escape(keyword)}")) as response_info:
        search_input.fill(keyword)
        search_input.press("Enter")

    response = response_info.value

    assert response.status == 200
    assert keyword in response.url
