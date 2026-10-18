"""
The auth-mode ladder, every rung set in-process: environment, .env.<env>,
environments.json, default.
"""

from __future__ import annotations

import json

import pytest

from core.auth.settings import AuthMode, resolve_auth_mode
from core.config_reader import ConfigReader


@pytest.fixture
def ladder(tmp_path, monkeypatch):
    """Build a real ConfigReader over temp files; each rung is opt-in."""
    monkeypatch.delenv("AUTH_MODE", raising=False)

    def build(*, os_env=None, env_file=None, committed=None) -> ConfigReader:
        entry = {"base_url": "https://qa.example.test/"}
        if committed is not None:
            entry["auth_mode"] = committed
        environments = tmp_path / "environments.json"
        environments.write_text(json.dumps({"qa": entry}))
        dotenv = tmp_path / ".env.qa"
        if env_file is not None:
            dotenv.write_text(f"AUTH_MODE={env_file}\n")
        elif dotenv.exists():
            dotenv.unlink()
        if os_env is not None:
            monkeypatch.setenv("AUTH_MODE", os_env)
        return ConfigReader("qa", environments_file=environments, env_file_dir=tmp_path)

    return build


def test_os_environment_beats_every_other_rung(ladder):
    config = ladder(os_env="token", env_file="password", committed="ui")
    assert resolve_auth_mode(config) is AuthMode.TOKEN
    assert config.source_of("auth_mode") == "env"


def test_env_file_beats_the_committed_value(ladder):
    config = ladder(env_file="client_credentials", committed="ui")
    assert resolve_auth_mode(config) is AuthMode.CLIENT_CREDENTIALS
    assert config.source_of("auth_mode") == "env_file"


def test_committed_value_beats_the_default(ladder):
    assert resolve_auth_mode(ladder(committed="ui")) is AuthMode.UI


def test_default_when_no_rung_answers(ladder):
    assert resolve_auth_mode(ladder()) is AuthMode.NONE
    assert resolve_auth_mode(ladder(), default=AuthMode.UI) is AuthMode.UI


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_override_is_ignored_at_every_rung(ladder, blank):
    config = ladder(os_env=blank, env_file=blank, committed="password")
    assert resolve_auth_mode(config) is AuthMode.PASSWORD


@pytest.mark.parametrize("raw", ["UI", "Ui", " ui ", "CLIENT-CREDENTIALS", "Client_Credentials"])
def test_matching_is_case_and_separator_insensitive(ladder, raw):
    assert resolve_auth_mode(ladder(os_env=raw)).value in {"ui", "client_credentials"}


def test_an_unknown_value_fails_naming_the_valid_modes(ladder):
    """A misspelled mode must not silently select a different login flow."""
    with pytest.raises(ValueError, match=r"Unknown auth mode 'tokn'.*'none'.*'token'"):
        resolve_auth_mode(ladder(os_env="tokn", committed="ui"))
