import json

import pytest
from playwright.sync_api import expect

from ui.pages.home_page import HomePage
from ui.pages.lot_page import LotPage
from ui.pages.search_results_page import SearchResultsPage


@pytest.fixture(scope="module")
def test_data():
    with open("ui/testdata/test_data.json") as f:
        return json.load(f)


@pytest.fixture
def home(page, config):
    hp = HomePage(page, config)
    hp.navigate()
    hp.accept_cookies()
    hp.close_register_popup_if_present()
    return hp


class TestSearch:
    @pytest.mark.smoke
    def test_search_returns_results(self, home, page, config, test_data):
        keyword = test_data["valid_search"]
        home.search(keyword)

        results = SearchResultsPage(page, config)
        results.verify_results_page_loaded(keyword)

    @pytest.mark.smoke
    def test_search_navigates_to_lot(self, home, page, config, test_data):
        keyword = test_data["valid_search"]
        home.search(keyword)

        results = SearchResultsPage(page, config)
        expect(results.resolve("lot_cards").first).to_be_visible(timeout=10_000)
        results.click_second_lot()

        lot = LotPage(page, config)
        lot.verify_lot_page_loaded()

    @pytest.mark.regression
    def test_search_url_reflects_keyword(self, home, page, config, test_data):
        keyword = test_data["valid_search"]
        home.search(keyword)

        expect(page).to_have_url(f"https://www.catawiki.com/en/s?q={keyword}", timeout=10_000)

    @pytest.mark.regression
    def test_search_unrecognized_query(self, home, page, config):
        home.search("xyznonexistent99999catawiki")

        results = SearchResultsPage(page, config)
        results.verify_unrecognized_search()

    @pytest.mark.regression
    def test_lot_page_shows_title_and_bid(self, home, page, config, test_data):
        keyword = test_data["valid_search"]
        home.search(keyword)

        results = SearchResultsPage(page, config)
        expect(results.resolve("lot_cards").first).to_be_visible(timeout=10_000)
        results.click_second_lot()

        lot = LotPage(page, config)
        lot.verify_lot_page_loaded()

        title = lot.get_title()
        assert title, "Lot title should not be empty"

        bid_type, amount = lot.get_current_bid()
        assert bid_type, "Bid type should be present"
        assert "€" in amount, "Bid amount should contain euro symbol"

    @pytest.mark.regression
    def test_search_input_visible_on_homepage(self, home, page, config):
        expect(home.resolve("search_input").first).to_be_visible()

    @pytest.mark.regression
    def test_multiple_lot_cards_returned(self, home, page, config, test_data):
        keyword = test_data["valid_search"]
        home.search(keyword)

        results = SearchResultsPage(page, config)
        page.wait_for_load_state("networkidle")
        lot_cards = results.resolve("lot_cards")
        expect(lot_cards.first).to_be_visible(timeout=10_000)
        assert lot_cards.count() > 1, "Search should return more than one result"
