"""A rejected password stops every later login as that user in the process, and only that."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from core.auth.settings import AuthMode, AuthSettings
from tests.ui.test_components_interaction import _LoginPage
from ui.components import universal_login
from ui.components.universal_login import (
    DEFAULT_PASSWORD_STEP,
    LoginError,
    PasswordRejected,
    UniversalLoginPage,
    is_password_rejection,
    mark_rejected,
    password_step_pattern,
    refuse_if_rejected,
    was_rejected,
)
from ui.fixtures.session_fixtures import storage_state_for

STEP = re.compile(DEFAULT_PASSWORD_STEP)
PASSWORD_URL = "https://login.example.test/u/login/password?state=abc"


@pytest.fixture(autouse=True)
def fresh_latch(monkeypatch):
    monkeypatch.setattr(universal_login, "_rejected", set())
    monkeypatch.delenv("LOGIN_PASSWORD_STEP_PATTERN", raising=False)


class TestMatcher:
    def test_a_400_answer_to_the_password_post_is_a_rejection(self):
        assert is_password_rejection("POST", PASSWORD_URL, 400, STEP)
        assert is_password_rejection(
            "post", "https://login.example.test/login/password/", 400, STEP
        )

    @pytest.mark.parametrize(
        "method, url, status",
        [
            ("POST", PASSWORD_URL, 200),  # accepted
            ("POST", PASSWORD_URL, 302),  # redirect onwards
            ("POST", PASSWORD_URL, 429),  # throttled: not a verdict on the password
            ("POST", PASSWORD_URL, 500),
            ("GET", PASSWORD_URL, 400),
            ("POST", "https://login.example.test/u/login/identifier", 400),
            ("POST", "https://app.example.test/api/orders?next=/login/password", 400),
        ],
    )
    def test_anything_less_definite_is_not(self, method, url, status):
        assert not is_password_rejection(method, url, status, STEP)

    def test_the_step_pattern_is_configurable(self):
        pattern = password_step_pattern({"LOGIN_PASSWORD_STEP_PATTERN": r"/signin/secret$"})
        assert is_password_rejection("POST", "https://idp.example.test/signin/secret", 400, pattern)
        assert password_step_pattern({}).pattern == DEFAULT_PASSWORD_STEP


class TestLatch:
    def test_case_and_whitespace_do_not_matter(self):
        mark_rejected("  User1@Example.test ")
        assert was_rejected("user1@example.test")
        assert not was_rejected("user2@example.test")
        with pytest.raises(PasswordRejected, match="not attempted.*Not retrying"):
            refuse_if_rejected("USER1@example.test")

    def test_a_rejection_is_a_login_error(self):
        assert issubclass(PasswordRejected, LoginError)


class _Response:
    def __init__(self, url, status, method="POST"):
        self.url, self.status = url, status
        self.request = SimpleNamespace(method=method)


class _IdpPage(_LoginPage):
    """The fake login page, plus response events: the password POST answers ``status``."""

    def __init__(self, status: int):
        super().__init__(identifier_first=False)
        self.status = status
        self.listeners = []
        self.gotos = 0

    def on(self, event, handler):
        assert event == "response"
        self.listeners.append(handler)

    def goto(self, url):
        self.gotos += 1

    def click(self, key):
        super().click(key)
        if key == "submit" and "password" in self.filled:
            for handler in self.listeners:
                handler(_Response(PASSWORD_URL, self.status))

    def wait_for_load_state(self, state, timeout=None):
        pass


def _login(page, username="user1@example.test"):
    UniversalLoginPage(page).login(
        "https://app.example.test/", username, "wrong", app_url="https://app.example.test/"
    )


class TestLoginFlow:
    def test_rejection_stops_the_login_and_every_later_one(self):
        first = _IdpPage(400)
        with pytest.raises(PasswordRejected, match="rejected"):
            _login(first)
        assert first.waited_for is None  # stopped before waiting for the app
        later = _IdpPage(200)
        with pytest.raises(PasswordRejected, match="not attempted"):
            _login(later, " USER1@example.test")
        assert later.gotos == 0 and later.clicks == []

    def test_another_user_is_not_affected(self):
        with pytest.raises(PasswordRejected):
            _login(_IdpPage(400))
        page = _IdpPage(200)
        _login(page, "user2@example.test")
        assert page.waited_for is not None

    def test_an_accepted_password_latches_nothing(self):
        _login(_IdpPage(200))
        assert not was_rejected("user1@example.test")

    def test_a_page_without_events_keeps_the_old_behaviour(self):
        page = _LoginPage(identifier_first=False)  # no .on, no wait_for_load_state
        UniversalLoginPage(page).login("https://app.example.test/", "user1@example.test", "pw")
        assert page.waited_for is not None and not was_rejected("user1@example.test")

    def test_the_session_fixture_refuses_before_opening_a_browser(self):
        mark_rejected("user1@example.test")
        settings = AuthSettings(
            AuthMode.UI, "https://app.example.test/", "u", "user1@example.test", "pw"
        )
        with pytest.raises(PasswordRejected):
            storage_state_for(object(), settings, None, None)  # a browser would be touched next
