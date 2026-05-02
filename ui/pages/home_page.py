from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.base_page import BasePage

# Block Usercentrics CMP at the network level — the aside uses Shadow DOM so
# JS removal races against the SDK re-inserting it; preventing the script from
# loading is the only reliable approach.
_CMP_BLOCK_PATTERNS = [
    "**/usercentrics.eu/**",
    "**/usercentrics.com/**",
    "**privacy-proxy.usercentrics.eu/**",
    "**aggregator.service.usercentrics**",
]


class HomePage(BasePage):
    def navigate(self):
        for pattern in _CMP_BLOCK_PATTERNS:
            self.page.route(pattern, lambda route: route.abort())
        self.page.goto(self.config.get_base_url())

    def accept_cookies(self):
        # CMP is blocked at network level; this is a safety net for environments
        # where a different consent banner may still appear.
        try:
            self.page.locator("#usercentrics-cmp-ui").wait_for(state="attached", timeout=3000)
            self.page.evaluate("""
                document.getElementById('usercentrics-cmp-ui')?.remove();
                document.querySelectorAll('[id^="usercentrics"]').forEach(el => el.remove());
                document.body.style.overflow = '';
            """)
        except PlaywrightTimeoutError:
            pass

    def close_register_popup_if_present(self):
        try:
            self.resolve("register_popup_close").click(timeout=3000)
        except PlaywrightTimeoutError:
            pass

    def search(self, keyword):
        self.resolve("search_input").first.fill(keyword)
        self.resolve("search_button").click()
