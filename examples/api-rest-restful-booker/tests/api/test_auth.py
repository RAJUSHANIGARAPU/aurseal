"""``POST /auth``: the token is only handed out for the right credentials."""

from __future__ import annotations

import pytest

from api_rest_restful_booker.client import ApiClient
from api_rest_restful_booker.services import AuthenticationFailed, AuthService
from api_rest_restful_booker.settings import Settings

pytestmark = pytest.mark.regression


@pytest.mark.smoke
def test_admin_credentials_return_a_token(api: ApiClient, settings: Settings):
    token = AuthService(api).token(
        settings.admin_username, settings.admin_password.get_secret_value()
    )
    assert token.strip()


@pytest.mark.parametrize(
    ("username", "password"),
    [("admin", "wrong"), ("nobody", "password123"), ("", "")],
    ids=["wrong-password", "unknown-user", "empty"],
)
def test_bad_credentials_get_no_token(api: ApiClient, username, password):
    with pytest.raises(AuthenticationFailed, match="Bad credentials"):
        AuthService(api).token(username, password)
