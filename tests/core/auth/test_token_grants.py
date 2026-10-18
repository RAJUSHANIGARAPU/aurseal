"""Password, client-credentials and injected-token grants, dispatched on the resolved mode."""

from __future__ import annotations

import pytest

from core.auth.secrets import SecretNotFound
from core.auth.settings import AuthMode, AuthSettings
from core.auth.token_grants import (
    TokenGrantError,
    client_credentials,
    is_authorised_for,
    password_grant,
    resolve_token,
    supplied_token,
)
from tests.core.auth._token_server import TokenServer
from tests.core.test_session_token_store import NOW, make_jwt


def _settings(mode: AuthMode, **kw) -> AuthSettings:
    return AuthSettings(
        mode=mode,
        login_url="https://app.example.test/",
        post_login_url="https://app.example.test/",
        **kw,
    )


def _issue(form, headers):
    if form.get("grant_type") == "password" and form.get("password") == "s3cret":
        return 200, {"access_token": "pw-token", "token_type": "Bearer"}
    if form.get("grant_type") == "client_credentials" and form.get("client_secret") == "m2m":
        return 200, {"access_token": "m2m-token", "token_type": "Bearer"}
    return 403, {"error": "access_denied", "error_description": "grant not enabled"}


class TestPasswordGrant:
    def test_posts_the_rfc_6749_form_and_returns_the_access_token(self):
        with TokenServer(_issue) as server:
            settings = _settings(
                AuthMode.PASSWORD,
                username="tester@example.test",
                password="s3cret",
                client_id="cli-1",
                audience="https://api.example.test",
                token_url=server.url,
            )
            assert password_grant(settings) == "pw-token"
        form = server.forms[0]
        assert form == {
            "grant_type": "password",
            "username": "tester@example.test",
            "password": "s3cret",
            "client_id": "cli-1",
            "audience": "https://api.example.test",
        }
        assert server.headers[0]["content-type"] == "application/x-www-form-urlencoded"

    def test_a_disabled_grant_names_the_provider_error_without_the_password(self):
        with TokenServer(_issue) as server:
            settings = _settings(
                AuthMode.PASSWORD,
                username="u",
                password="wrong-pass",
                client_id="c",
                token_url=server.url,
            )
            with pytest.raises(TokenGrantError) as caught:
                password_grant(settings)
        message = str(caught.value)
        assert "HTTP 403" in message and "access_denied" in message
        assert "wrong-pass" not in message

    def test_missing_settings_are_named_by_variable(self):
        with pytest.raises(TokenGrantError, match="AUTH_USERNAME, AUTH_PASSWORD, AUTH_CLIENT_ID"):
            password_grant(_settings(AuthMode.PASSWORD, token_url="http://127.0.0.1:1/t"))

    def test_missing_token_url(self):
        settings = _settings(AuthMode.PASSWORD, username="u", password="p", client_id="c")
        with pytest.raises(TokenGrantError, match="AUTH_TOKEN_URL"):
            password_grant(settings)

    def test_success_without_a_token_is_an_error(self):
        with TokenServer(lambda f, h: (200, {"token_type": "Bearer"})) as server:
            settings = _settings(
                AuthMode.PASSWORD, username="u", password="p", client_id="c", token_url=server.url
            )
            with pytest.raises(TokenGrantError, match="without an access_token"):
                password_grant(settings)


class TestClientCredentials:
    def test_secret_from_settings(self):
        with TokenServer(_issue) as server:
            settings = _settings(
                AuthMode.CLIENT_CREDENTIALS,
                client_id="svc",
                client_secret="m2m",
                scope="read write",
                organization="tenant-7",
                token_url=server.url,
            )
            assert client_credentials(settings) == "m2m-token"
        assert server.forms[0] == {
            "grant_type": "client_credentials",
            "client_id": "svc",
            "client_secret": "m2m",
            "scope": "read write",
            "organization": "tenant-7",
        }

    def test_blank_secret_is_looked_up(self):
        asked = []

        def lookup(name):
            asked.append(name)
            return "m2m"

        with TokenServer(_issue) as server:
            settings = _settings(AuthMode.CLIENT_CREDENTIALS, client_id="svc", token_url=server.url)
            assert client_credentials(settings, secret_lookup=lookup) == "m2m-token"
        assert asked == ["AUTH_CLIENT_SECRET"]

    def test_unfindable_secret_fails_before_any_request(self):
        def lookup(name):
            raise SecretNotFound(f"secret {name} not found")

        with TokenServer(_issue) as server:
            settings = _settings(AuthMode.CLIENT_CREDENTIALS, client_id="svc", token_url=server.url)
            with pytest.raises(SecretNotFound):
                client_credentials(settings, secret_lookup=lookup)
        assert server.forms == []

    def test_needs_a_client_id(self):
        with pytest.raises(TokenGrantError, match="AUTH_CLIENT_ID"):
            client_credentials(_settings(AuthMode.CLIENT_CREDENTIALS, token_url="http://x/t"))


class TestSuppliedToken:
    def test_a_valid_jwt_passes_through(self):
        token = make_jwt({"exp": NOW + 3600})
        settings = _settings(AuthMode.TOKEN, supplied_token=f"  {token} ")
        assert supplied_token(settings, now=NOW) == token

    def test_an_expired_token_fails_before_any_request(self):
        token = make_jwt({"exp": NOW - 5})
        with pytest.raises(TokenGrantError, match="SUPPLIED_TOKEN expired") as caught:
            supplied_token(_settings(AuthMode.TOKEN, supplied_token=token), now=NOW)
        assert token not in str(caught.value)

    def test_a_token_inside_the_margin_is_refused(self):
        token = make_jwt({"exp": NOW + 30})
        with pytest.raises(TokenGrantError, match="expires within 60s"):
            supplied_token(_settings(AuthMode.TOKEN, supplied_token=token), now=NOW)

    def test_a_jwt_without_exp_never_expires(self):
        token = make_jwt({"sub": "svc"})
        assert supplied_token(_settings(AuthMode.TOKEN, supplied_token=token), now=NOW) == token

    def test_unreadable_exp_is_refused(self):
        token = make_jwt({"exp": "soon"})
        with pytest.raises(TokenGrantError, match="unreadable exp"):
            supplied_token(_settings(AuthMode.TOKEN, supplied_token=token), now=NOW)

    @pytest.mark.parametrize("raw", ["opaque-session-id", "eyJ.not-base64!.x", "a.b"])
    def test_not_a_jwt(self, raw):
        with pytest.raises(TokenGrantError, match="not a readable JWT"):
            supplied_token(_settings(AuthMode.TOKEN, supplied_token=raw), now=NOW)

    def test_blank(self):
        with pytest.raises(TokenGrantError, match="needs SUPPLIED_TOKEN"):
            supplied_token(_settings(AuthMode.TOKEN))


class TestResolveToken:
    def test_none_mode_has_no_token(self):
        assert resolve_token(_settings(AuthMode.NONE)) is None

    def test_dispatches_on_the_mode(self):
        token = make_jwt({"exp": NOW + 3600})
        assert resolve_token(_settings(AuthMode.TOKEN, supplied_token=token), now=NOW) == token
        with TokenServer(_issue) as server:
            pw = _settings(
                AuthMode.PASSWORD,
                username="u",
                password="s3cret",
                client_id="c",
                token_url=server.url,
            )
            cc = _settings(
                AuthMode.CLIENT_CREDENTIALS,
                client_id="c",
                client_secret="m2m",
                token_url=server.url,
            )
            assert resolve_token(pw) == "pw-token"
            assert resolve_token(cc) == "m2m-token"

    def test_ui_mode_points_at_the_browser_login(self):
        with pytest.raises(TokenGrantError, match="login_once"):
            resolve_token(_settings(AuthMode.UI))

    def test_mode_comes_from_the_settings_ladder(self):
        from core.config_reader import ConfigReader, ConfigSources

        token = make_jwt({"exp": NOW + 3600})
        config = ConfigReader(
            "qa",
            sources=ConfigSources(
                os_env={"AUTH_MODE": "TOKEN", "SUPPLIED_TOKEN": token},
                file_config={"base_url": "https://app.example.test/"},
            ),
        )
        assert resolve_token(AuthSettings.from_config(config), now=NOW) == token


def test_is_authorised_for_probes_with_the_bearer():
    with TokenServer(_issue) as server:
        probe = server.url.replace("/oauth/token", "/api/ping")
        assert is_authorised_for("good", probe)
        assert not is_authorised_for("bad", probe)
