"""Auth settings ladder, session refresh, session injection and the UI login cache."""

from __future__ import annotations

import os

import pytest

from core.auth.injection import (
    apply_session,
    cookies_from_storage_state,
    inject_bearer_into_local_storage,
    restore_session_cookies,
)
from core.auth.session import SessionToken
from core.auth.session_token_store import SessionTokenStore
from core.auth.settings import AuthMode, AuthSettings, resolve_auth_mode
from core.auth.ui_login import (
    BearerTokenSniffer,
    bearer_from_headers,
    login_once,
    token_from_body,
    token_from_storage_values,
)
from core.config_reader import ConfigReader, ConfigSources
from tests.core.test_session_token_store import NOW, make_jwt


def _config(os_env=None, env_file=None, file_config=None) -> ConfigReader:
    return ConfigReader(
        "qa",
        sources=ConfigSources(os_env or {}, env_file or {}, file_config or {}),
    )


class TestAuthModeResolution:
    def test_environment_overrides_the_committed_mode(self):
        """One resolver: an exported AUTH_MODE beats the file, everywhere it is read."""
        config = _config({"AUTH_MODE": "token"}, {}, {"auth_mode": "ui"})
        assert resolve_auth_mode(config) is AuthMode.TOKEN

    def test_env_file_rung(self):
        assert (
            resolve_auth_mode(_config({}, {"AUTH_MODE": "ui"}, {"auth_mode": "none"}))
            is AuthMode.UI
        )

    def test_blank_export_does_not_shadow_the_file(self):
        assert resolve_auth_mode(_config({"AUTH_MODE": ""}, {}, {"auth_mode": "ui"})) is AuthMode.UI

    def test_default_is_none(self):
        assert resolve_auth_mode(_config()) is AuthMode.NONE

    @pytest.mark.parametrize("raw", ["UI", " ui ", "Client-Credentials"])
    def test_parsing_is_forgiving_about_case_and_dashes(self, raw):
        assert AuthMode.parse(raw) in (AuthMode.UI, AuthMode.CLIENT_CREDENTIALS)

    def test_unknown_mode_names_the_valid_ones(self):
        with pytest.raises(ValueError, match="expected one of"):
            AuthMode.parse("ropc-ish")


class TestAuthSettings:
    def test_built_from_the_ladder_with_login_url_defaulting_to_base(self):
        settings = AuthSettings.from_config(
            _config(
                {"AUTH_PASSWORD": "s3cret"},
                {"AUTH_USERNAME": "tester@example.test"},
                {"base_url": "https://app.example.test/", "auth_mode": "ui"},
            )
        )
        assert settings.mode is AuthMode.UI
        assert settings.login_url == "https://app.example.test/"
        assert settings.username == "tester@example.test"
        assert settings.missing_for_mode() == []

    def test_secrets_never_reach_the_repr(self):
        settings = AuthSettings(
            AuthMode.UI, "u", "u", password="s3cret", client_secret="c5", supplied_token="t9"
        )
        text = repr(settings)
        assert "s3cret" not in text and "c5" not in text and "t9" not in text

    def test_missing_settings_are_named_by_variable(self):
        settings = AuthSettings(AuthMode.CLIENT_CREDENTIALS, "u", "u", client_id="app")
        assert settings.missing_for_mode() == ["AUTH_TOKEN_URL", "AUTH_CLIENT_SECRET"]


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestSessionToken:
    def test_acquires_once_then_reuses(self, tmp_path):
        tokens = iter([make_jwt({"exp": NOW + 3600})])
        session = SessionToken(
            lambda: next(tokens), SessionTokenStore("k", tmp_path, clock=_Clock(NOW))
        )
        first = session.token()
        assert session.token() == first
        assert session.acquisitions == 1

    def test_refreshes_inside_the_margin(self, tmp_path):
        clock = _Clock(NOW)
        issued = [make_jwt({"exp": NOW + 600}), make_jwt({"exp": NOW + 5000})]
        session = SessionToken(
            lambda: issued.pop(0),
            SessionTokenStore("k", tmp_path, clock=clock),
            refresh_margin_s=120,
        )
        first = session.token()
        clock.now = NOW + 500  # 100 s left, inside the 120 s margin
        second = session.token()
        assert second != first
        assert session.acquisitions == 2

    def test_a_cached_token_from_an_earlier_run_is_reused(self, tmp_path):
        cache = SessionTokenStore("k", tmp_path, clock=_Clock(NOW))
        cache.save_token(make_jwt({"exp": NOW + 3600}))
        session = SessionToken(lambda: pytest.fail("must not log in"), cache)
        assert session.token()

    def test_supplied_token_is_never_refreshed(self, tmp_path):
        expired = make_jwt({"exp": NOW - 10})
        session = SessionToken(
            lambda: pytest.fail("no refresh"),
            SessionTokenStore("k", tmp_path, clock=_Clock(NOW)),
            mode=AuthMode.TOKEN,
            supplied_token=expired,
        )
        assert session.token() == expired

    def test_no_auth_mode_has_no_token(self, tmp_path):
        assert (
            SessionToken(lambda: "x", SessionTokenStore("k", tmp_path), mode=AuthMode.NONE).token()
            is None
        )

    def test_failed_acquisition_returns_none(self, tmp_path):
        session = SessionToken(lambda: None, SessionTokenStore("k", tmp_path, clock=_Clock(NOW)))
        assert session.token() is None


class _Context:
    def __init__(self) -> None:
        self.cookies: list[dict] = []

    def add_cookies(self, cookies) -> None:
        self.cookies.extend(cookies)


class _Page:
    def __init__(self) -> None:
        self.calls: list = []

    def evaluate(self, script, arg=None):
        self.calls.append((script, arg))


STATE = {
    "cookies": [
        {
            "name": "session",
            "value": "abc",
            "domain": "api.example.test",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "expires": NOW + 900,
            "sameSite": "Lax",
        },
        {
            "name": "XSRF-TOKEN",
            "value": "x1",
            "domain": ".example.test",
            "expires": -1,
            "sameSite": "Bogus",
        },
        {"name": "", "value": "dropped", "domain": "example.test"},
    ]
}


class TestInjection:
    def test_cookies_are_copied_exactly_as_issued(self):
        session, xsrf = cookies_from_storage_state(STATE)
        assert session == {
            "name": "session",
            "value": "abc",
            "domain": "api.example.test",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "expires": NOW + 900,
            "sameSite": "Lax",
        }
        assert xsrf["domain"] == ".example.test"  # the parent domain the page can read
        assert "expires" not in xsrf and "sameSite" not in xsrf

    def test_restore_reads_a_fresh_cache(self, tmp_path):
        cache = SessionTokenStore(
            "k", tmp_path, clock=lambda: os.path.getmtime(cache.storage_state_path) + 1
        )
        cache.save_storage_state(STATE)
        context = _Context()
        assert restore_session_cookies(context, cache) == 2
        assert {c["name"] for c in context.cookies} == {"session", "XSRF-TOKEN"}

    def test_restore_is_zero_without_a_cache(self, tmp_path):
        assert restore_session_cookies(_Context(), SessionTokenStore("k", tmp_path)) == 0

    def test_token_is_passed_as_an_argument_never_formatted_into_script(self):
        page = _Page()
        hostile = "eyJ'); alert(1); ('"
        inject_bearer_into_local_storage(page, hostile)
        script, arg = page.calls[0]
        assert hostile not in script
        assert arg == [hostile, ["access_token", "token"]]

    def test_apply_session_picks_the_mechanism_by_token_shape(self, tmp_path):
        page = _Page()
        jwt = make_jwt({"exp": NOW + 3600})
        assert (
            apply_session(_Context(), page, jwt, SessionTokenStore("k", tmp_path))
            == "local_storage"
        )
        assert apply_session(_Context(), page, "opaque", SessionTokenStore("k", tmp_path)) == "none"
        assert apply_session(_Context(), page, None, SessionTokenStore("k", tmp_path)) == "none"


class _Request:
    def __init__(self, headers):
        self.headers = headers


class _Response:
    def __init__(self, url, body):
        self.url = url
        self._body = body

    def text(self):
        return self._body


class TestTokenCapture:
    JWT = make_jwt({"exp": NOW + 3600})

    def test_bearer_header(self):
        assert bearer_from_headers({"Authorization": f"Bearer {self.JWT}"}) == self.JWT
        assert bearer_from_headers({"authorization": "Bearer not-a-jwt"}) is None
        assert bearer_from_headers({"accept": "x"}) is None

    def test_token_endpoint_body(self):
        assert token_from_body(f'{{"access_token": "{self.JWT}"}}') == self.JWT
        assert token_from_body({"id_token": "idt"}) == "idt"
        assert token_from_body("<html>") is None

    def test_storage_scan_finds_a_nested_jwt(self):
        assert token_from_storage_values(["x", f'{{"auth": {{"t": "{self.JWT}"}}}}']) == self.JWT

    def test_sniffer_keeps_the_first_token(self):
        sniffer = BearerTokenSniffer()
        sniffer._on_response(
            _Response("https://login.example.test/oauth/token", f'{{"access_token":"{self.JWT}"}}')
        )
        sniffer._on_request(_Request({"Authorization": f"Bearer {make_jwt({'exp': 1})}"}))
        assert sniffer.token == self.JWT and sniffer.source == "token endpoint"

    def test_sniffer_ignores_other_responses(self):
        sniffer = BearerTokenSniffer()
        sniffer._on_response(
            _Response("https://app.example.test/api/items", '{"access_token":"x"}')
        )
        assert sniffer.token is None


class _LoginPage:
    def __init__(self, token):
        self.token = token
        self.handlers: dict = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def evaluate(self, script):
        return []


class _LoginContext:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def storage_state(self):
        return {"cookies": [], "origins": []}

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self, token=None):
        self.token = token
        self.contexts: list = []

    def new_context(self, **kwargs):
        context = _LoginContext(_LoginPage(self.token))
        self.contexts.append(context)
        return context


class TestLoginOnce:
    def _login(self, page):
        if page.token:
            page.handlers["request"](_Request({"Authorization": f"Bearer {page.token}"}))

    def test_logs_in_caches_and_then_reuses(self, tmp_path):
        jwt = make_jwt({"exp": 4_000_000_000})
        cache = SessionTokenStore("k", tmp_path)
        browser = _Browser(jwt)
        first = login_once(browser, cache, self._login)
        assert (first.token, first.reused) == (jwt, False)
        assert browser.contexts[0].closed
        second = login_once(browser, cache, self._login)
        assert (second.token, second.reused) == (jwt, True)
        assert len(browser.contexts) == 1

    def test_cookie_only_login_is_reused_on_state_age(self, tmp_path):
        cache = SessionTokenStore("k", tmp_path)
        browser = _Browser(None)
        assert login_once(browser, cache, self._login).token is None
        assert login_once(browser, cache, self._login).reused
        assert len(browser.contexts) == 1

    def test_expired_token_forces_a_new_login(self, tmp_path):
        cache = SessionTokenStore("k", tmp_path)
        cache.save_token(make_jwt({"exp": 1}))
        cache.save_storage_state({})
        browser = _Browser(make_jwt({"exp": 4_000_000_000}))
        assert not login_once(browser, cache, self._login).reused
