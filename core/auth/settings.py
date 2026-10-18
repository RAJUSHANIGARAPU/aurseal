"""
Every authentication setting, resolved through one ladder.

Settings come from ``ConfigReader`` and therefore from the same precedence as
everything else: ``AUTH_MODE`` in the environment, then ``.env.<env>``, then the
``auth_mode`` key in ``environments.json``, then the default. The point of routing
auth through the shared ladder rather than reading ``os.environ`` here is that
there is then exactly one answer to "which mode is this run using". When two call
sites resolve it two ways, one of them reports a mode the run is not using — and
the place that reports it is usually the report header a reader trusts most.

Secrets (``password``, ``client_secret``, ``supplied_token``) are excluded from
``repr`` so a settings object can be logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.config_reader import ConfigReader


class AuthMode(str, Enum):
    NONE = "none"  # the system under test needs no login
    UI = "ui"  # log in through the hosted login page in a browser
    PASSWORD = "password"  # resource-owner password grant against the token endpoint
    CLIENT_CREDENTIALS = "client_credentials"  # machine-to-machine token
    TOKEN = "token"  # a token handed in from outside (SUPPLIED_TOKEN); never refreshed

    @classmethod
    def parse(cls, raw: object) -> AuthMode:
        text = str(raw or "").strip().lower().replace("-", "_")
        for mode in cls:
            if mode.value == text:
                return mode
        raise ValueError(f"Unknown auth mode {raw!r}; expected one of {[m.value for m in cls]}")


def resolve_auth_mode(config: ConfigReader, default: AuthMode = AuthMode.NONE) -> AuthMode:
    """The mode this run will actually use. Use this everywhere the mode is needed."""
    return AuthMode.parse(config.get("auth_mode", default.value))


@dataclass(frozen=True)
class AuthSettings:
    mode: AuthMode
    login_url: str
    post_login_url: str
    username: str = ""
    password: str = field(default="", repr=False)
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    audience: str = ""
    scope: str = ""
    token_url: str = ""
    organization: str = ""
    token_cookie_name: str = ""
    headless: bool = True
    supplied_token: str = field(default="", repr=False)
    session_ttl_s: int = 900

    @classmethod
    def from_config(cls, config: ConfigReader) -> AuthSettings:
        base_url = str(config.get("base_url", ""))
        login_url = str(config.get("login_url", base_url))

        def text(key: str, default: str = "") -> str:
            return str(config.get(key, default))

        return cls(
            mode=resolve_auth_mode(config),
            login_url=login_url,
            post_login_url=text("post_login_url", base_url),
            username=text("auth_username"),
            password=text("auth_password"),
            client_id=text("auth_client_id"),
            client_secret=text("auth_client_secret"),
            audience=text("auth_audience"),
            scope=text("auth_scope"),
            token_url=text("auth_token_url"),
            organization=text("auth_organization"),
            token_cookie_name=text("auth_token_cookie_name"),
            headless=config.get_bool("auth_headless", True),
            supplied_token=text("supplied_token"),
            session_ttl_s=config.get_int("auth_session_ttl_seconds", 900),
        )

    def cache_key(self) -> str:
        """Identifies the login a cached token belongs to; no secrets in it."""
        return "_".join(p for p in (self.login_url, self.audience, self.username) if p)

    def missing_for_mode(self) -> list[str]:
        """Settings the chosen mode needs but does not have, by environment-variable name."""
        required = {
            AuthMode.UI: {"AUTH_USERNAME": self.username, "AUTH_PASSWORD": self.password},
            AuthMode.PASSWORD: {
                "AUTH_USERNAME": self.username,
                "AUTH_PASSWORD": self.password,
                "AUTH_TOKEN_URL": self.token_url,
                "AUTH_CLIENT_ID": self.client_id,
            },
            AuthMode.CLIENT_CREDENTIALS: {
                "AUTH_TOKEN_URL": self.token_url,
                "AUTH_CLIENT_ID": self.client_id,
                "AUTH_CLIENT_SECRET": self.client_secret,
            },
            AuthMode.TOKEN: {"SUPPLIED_TOKEN": self.supplied_token},
        }.get(self.mode, {})
        return [name for name, value in required.items() if not value]
