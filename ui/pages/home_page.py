from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.base_page import BasePage

# Block Usercentrics CMP at the network level — the aside uses Shadow DOM so
# JS removal races against the SDK re-inserting it; preventing the script from
# loading is the only reliable approach.
# Broad wildcard catches all subdomains and CDN variants (e.g. app.usercentrics.eu,
# privacy-proxy.usercentrics.eu, cdn.usercentrics.com).
_CMP_BLOCK_PATTERNS = [
    "**usercentrics.eu**",
    "**usercentrics.com**",
]

_CMP_REMOVAL_JS = """
    document.getElementById('usercentrics-cmp-ui')?.remove();
    document.querySelectorAll('[id^="usercentrics"]').forEach(el => el.remove());
    document.body.style.overflow = '';
"""


class HomePage(BasePage):
    def navigate(self):
        for pattern in _CMP_BLOCK_PATTERNS:
            self.page.route(pattern, lambda route: route.abort())
        self.page.goto(self.config.get_base_url())
        self.page.wait_for_load_state("domcontentloaded")

    def accept_cookies(self):
        aside = self.page.locator("#usercentrics-cmp-ui")
        try:
            # On CI (cold browser, no cache) the CMP can load after domcontentloaded —
            # use a longer timeout so we don't return before it appears.
            aside.wait_for(state="attached", timeout=8000)
            self.page.evaluate(_CMP_REMOVAL_JS)
            # Wait for confirmed removal before allowing any further interaction.
            aside.wait_for(state="detached", timeout=5000)
        except PlaywrightTimeoutError:
            # CMP never appeared — network block worked or consent already stored.
            pass

    def close_register_popup_if_present(self):
        try:
            self.resolve("register_popup_close").click(timeout=3000)
        except PlaywrightTimeoutError:
            pass

    def search(self, keyword):
        self.resolve("search_input").first.fill(keyword)
        self.resolve("search_button").click()
