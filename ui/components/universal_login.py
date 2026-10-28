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
  provider and matches the app, not when some element appears;
* **a rejected password stops the process** — when the password form's POST is
  answered with HTTP 400, the username is recorded and every later login for it in
  this process fails at once, without opening a page. Every test starting its own
  login would otherwise turn one wrong password into one failed attempt per test,
  and identity providers block an account after a handful. Only that definite
  answer latches; a slow page or an unknown step keeps the old behaviour. The
  password step is matched on the request path (``LOGIN_PASSWORD_STEP_PATTERN``, a
  regex; default: the path ends in ``/login/password``).

Locators live in ``ui/components/locators/universal_login.json`` (label and role
patterns), so a page with other wording is a JSON change, not a code change.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from core.base_page import BasePage
from core.logger import logger

LOCATOR_FILE = Path(__file__).parent / "locators" / "universal_login.json"
PASSWORD_STEP_ENV = "LOGIN_PASSWORD_STEP_PATTERN"
DEFAULT_PASSWORD_STEP = r"/login/password/?$"
REJECTED_STATUS = 400

# Usernames whose password this process has seen rejected (folded: trimmed, lower case).
_rejected: set[str] = set()


class LoginError(AssertionError):
    pass


class PasswordRejected(LoginError):
    """The identity provider refused the password; this process will not try it again."""


def _fold(username: str) -> str:
    return (username or "").strip().lower()


def password_step_pattern(environ: Mapping[str, str] | None = None) -> re.Pattern[str]:
    raw = (os.environ if environ is None else environ).get(PASSWORD_STEP_ENV, "").strip()
    return re.compile(raw or DEFAULT_PASSWORD_STEP)


def is_password_rejection(method: str, url: str, status: int, pattern: re.Pattern[str]) -> bool:
    """A POST to the password step answered with 400: the definite "wrong password"."""
    return (
        (method or "").upper() == "POST"
        and status == REJECTED_STATUS
        and bool(pattern.search(urlsplit(url or "").path))
    )


def mark_rejected(username: str) -> None:
    _rejected.add(_fold(username))


def was_rejected(username: str) -> bool:
    return _fold(username) in _rejected


def clear_rejections() -> None:
    """Forget every rejection (tests; a new process starts empty anyway)."""
    _rejected.clear()


def refuse_if_rejected(username: str) -> None:
    """Raise PasswordRejected when this process already saw ``username``'s password fail."""
    if was_rejected(username):
        raise PasswordRejected(
            f"login as {username.strip()} not attempted: its password was rejected earlier in "
            "this process (HTTP 400 from the password step). Not retrying, because each "
            "attempt counts towards the identity provider's lockout for the account; fix the "
            "password and start a new run"
        )


class UniversalLoginPage(BasePage):
    def __init__(
        self,
        page,
        config=None,
        locator_file: str | Path = LOCATOR_FILE,
        password_step: re.Pattern[str] | None = None,
    ) -> None:
        super().__init__(page, config, locator_file)
        self._password_step = password_step or password_step_pattern()
        self._rejected_seen = False
        self._watching = False

    def _on_response(self, response) -> None:
        try:
            if is_password_rejection(
                response.request.method, response.url, response.status, self._password_step
            ):
                self._rejected_seen = True
        except Exception:  # noqa: BLE001 - a listener must never break the page
            return

    def watch_password_step(self) -> None:
        """Listen for the password POST's answer (once per page)."""
        on = getattr(self.page, "on", None)
        if callable(on) and not self._watching:
            on("response", self._on_response)
            self._watching = True

    def _stop_if_rejected(self, username: str, cause: Exception | None = None) -> None:
        if not self._rejected_seen:
            return
        mark_rejected(username)
        raise PasswordRejected(
            f"password for {username.strip()} rejected (HTTP 400 from the password step); "
            "later logins as this user in this process fail at once instead of adding "
            "attempts towards the account's lockout"
        ) from cause

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
        self.watch_password_step()
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
        # Checked here, before anything could reload the page: reloading the answer to a
        # POST can submit the wrong password a second time.
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 - not knowing yet is not a rejection
            pass
        self._stop_if_rejected(username)

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
        refuse_if_rejected(username)
        self.watch_password_step()
        self.navigate_with_retry(url)
        self.fill_credentials(username, password)
        if organization:
            self.choose_organization(organization)
        try:
            self.wait_for_app(app_url or url)
        except LoginError as exc:
            self._stop_if_rejected(username, exc)  # the 400 may have landed after the check
            raise
