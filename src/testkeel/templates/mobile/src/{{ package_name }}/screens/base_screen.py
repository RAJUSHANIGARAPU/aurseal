"""What every screen object shares: accessibility-id locators and explicit waits."""

from __future__ import annotations

from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.webdriver import WebDriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait

Locator = tuple[str, str]


def accessibility_id(value: str) -> Locator:
    """The preferred locator: what a screen reader announces, the same id on Android and iOS."""
    return (AppiumBy.ACCESSIBILITY_ID, value)


def android_text(fragment: str) -> Locator:
    """Fallback for elements the app gives no accessibility id, such as inline errors."""
    escaped = fragment.replace("\\", "\\\\").replace('"', '\\"')
    return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped}")')


class BaseScreen:
    """A screen that is showing when ``marker`` is visible.

    Screen objects locate and act; they never assert. Every lookup waits for an explicit
    condition, so there is no implicit wait and no sleep anywhere.
    """

    marker: Locator

    def __init__(self, driver: WebDriver, timeout_s: float = 10.0) -> None:
        self.driver = driver
        self.wait = WebDriverWait(driver, timeout_s)

    def element(self, locator: Locator) -> WebElement:
        return self.wait.until(
            ec.visibility_of_element_located(locator), f"not visible: {locator[1]}"
        )

    def tap(self, locator: Locator) -> None:
        self.element(locator).click()

    def type_into(self, locator: Locator, text: str) -> None:
        field = self.element(locator)
        field.clear()
        field.send_keys(text)

    def is_visible(self, locator: Locator) -> bool:
        """True once ``locator`` is visible, False when the wait runs out."""
        try:
            self.element(locator)
        except TimeoutException:
            return False
        return True

    def is_showing(self) -> bool:
        return self.is_visible(self.marker)
