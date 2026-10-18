"""
Page object for a hosted OAuth / OIDC "universal login" page.

Hosted login pages differ in detail but share a shape this handles:

* **identifier first** — some ask for the username, then show the password on a
  second screen; others show both at once. The username is filled, and the submit
  is clicked only if the password field is not already there;
* **hidden decoys** — some pages render an invisible copy of a field for password
  managers; only the *visible* field is filled;
* **organisation picker** — multi-tenant identity providers may ask which
  organisation to log into after the password;
* **landing** — the login has finished when the URL has left the identity
  provider and matches the app, not when some element appears.

Locators live in ``ui/components/locators/universal_login.json`` (label and role
patterns), so a page with other wording is a JSON change, not a code change.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.base_page import BasePage
from core.logger import logger

LOCATOR_FILE = Path(__file__).parent / "locators" / "universal_login.json"


class LoginError(AssertionError):
    pass


class UniversalLoginPage(BasePage):
    def __init__(self, page, config=None, locator_file: str | Path = LOCATOR_FILE) -> None:
        super().__init__(page, config, locator_file)

    def _visible(self, key: str):
        return self.resolve(key).filter(visible=True).first

    def navigate_with_retry(self, url: str, attempts: int = 3, backoff_ms: int = 2000) -> None:
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self.page.goto(url)
                return
            except Exception as exc:  # noqa: BLE001 - retried, then raised with context
                last = exc
                first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                logger.warning("navigate attempt %d/%d failed: %s", attempt, attempts, first_line)
                if attempt < attempts:
                    self.page.wait_for_timeout(backoff_ms * attempt)
        raise LoginError(f"navigation to {url} failed after {attempts} attempts") from last

    def fill_credentials(self, username: str, password: str, timeout_ms: int = 10000) -> None:
        user = self._visible("username_field")
        user.wait_for(timeout=timeout_ms)
        user.fill(username)
        password_field = self._visible("password_field")
        if password_field.count() == 0:
            self._visible(
                "submit_button"
            ).click()  # identifier-first: password is on the next screen
        password_field.wait_for(timeout=timeout_ms)
        password_field.fill(password)
        self._visible("submit_button").click()

    def choose_organization(self, name: str, timeout_ms: int = 8000) -> bool:
        """Pick ``name`` on an organisation picker; False (logged) when no picker showed it."""
        if not name:
            return False
        option = self.page.get_by_role("button", name=name, exact=True)
        if option.count() == 0:
            option = self.page.get_by_role("link", name=name, exact=True)
        try:
            option.first.click(timeout=timeout_ms)
            return True
        except Exception as exc:  # noqa: BLE001 - not every login has a picker
            logger.info("no organisation picker entry %r (%s)", name, str(exc).splitlines()[0])
            return False

    def wait_for_app(self, app_url: str | re.Pattern[str], timeout_ms: int = 20000) -> None:
        """Wait until the URL is the app's (a glob, regex, or URL prefix)."""
        target = app_url
        if isinstance(app_url, str) and "*" not in app_url:
            target = re.compile("^" + re.escape(app_url.rstrip("/")))
        try:
            self.page.wait_for_url(target, timeout=timeout_ms)
        except Exception as exc:
            raise LoginError(f"login did not land on {app_url} (still at {self.page.url})") from exc

    def login(
        self,
        url: str,
        username: str,
        password: str,
        *,
        app_url: str | re.Pattern[str] | None = None,
        organization: str = "",
    ) -> None:
        """The whole flow: open, credentials, optional organisation, landing."""
        if not username or not password:
            raise LoginError(
                "login needs a username and a password (AUTH_USERNAME / AUTH_PASSWORD)"
            )
        self.navigate_with_retry(url)
        self.fill_credentials(username, password)
        if organization:
            self.choose_organization(organization)
        self.wait_for_app(app_url or url)
