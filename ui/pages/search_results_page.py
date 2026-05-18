import re

from playwright.sync_api import expect

from core.base_page import BasePage


class SearchResultsPage(BasePage):
    def click_second_lot(self):
        lots = self.resolve("lot_cards")
        lots.nth(1).click()

    def verify_results_page_loaded(self, keyword: str):
        expect(self.page).to_have_url(re.compile(rf"/en/s\?q={keyword}"))
        expect(self.resolve("lot_cards").first).to_be_visible()
        expect(self.page.get_by_role("heading", level=1)).to_contain_text(keyword)

    def verify_unrecognized_search(self):
        # Catawiki always shows related objects rather than a "0 results" state.
        # For unrecognized queries the count heading ("N related objects") is shown
        # instead of exact-match results, which confirms the search was processed.
        expect(self.resolve("search_results_count")).to_be_visible(timeout=10_000)
