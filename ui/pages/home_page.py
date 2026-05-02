from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.base_page import BasePage

_CMP_BUTTON_NAMES = ["Agree", "Accept", "Accept all", "I agree", "Allow all", "OK"]


class HomePage(BasePage):
    def navigate(self):
        base_url = self.config.get_base_url()
        self.page.goto(base_url)

    def accept_cookies(self):
        cmp = self.page.locator("#usercentrics-cmp-ui")
        # The CMP aside is often empty (dialog lives in its Shadow DOM) so
        # Playwright's "visible" check times out — use "attached" instead.
        try:
            cmp.wait_for(state="attached", timeout=5000)
        except PlaywrightTimeoutError:
            return  # no consent banner on this environment

        # Try common accept-button labels — Catawiki's CMP text varies by region
        for label in _CMP_BUTTON_NAMES:
            try:
                btn = self.page.get_by_role("button", name=label)
                btn.wait_for(state="visible", timeout=2000)
                btn.click()
                break
            except PlaywrightTimeoutError:
                continue

        # Always force-remove the element and any Usercentrics backdrop nodes,
        # then reset scroll-lock styles — regardless of whether the click worked.
        self.page.evaluate("""
            document.getElementById('usercentrics-cmp-ui')?.remove();
            document.querySelectorAll(
                '[id^="usercentrics"], [class*="uc-backdrop"], #uc-center-container'
            ).forEach(el => el.remove());
            document.body.style.overflow = '';
            document.body.style.height = '';
            document.body.style.position = '';
        """)

    def close_register_popup_if_present(self):
        try:
            self.resolve("register_popup_close").click(timeout=3000)
        except PlaywrightTimeoutError:
            pass

    def search(self, keyword):
        self.resolve("search_input").first.fill(keyword)
        self.resolve("search_button").click()
