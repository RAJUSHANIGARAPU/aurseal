"""The settings layer: defaults, environment overrides and rejected values."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from graphql_countries.settings import Settings

pytestmark = pytest.mark.offline


def test_defaults_load_without_any_environment(monkeypatch):
    monkeypatch.chdir("/")  # no .env in the working directory
    settings = Settings()
    assert settings.timeout_s > 0


def test_environment_variable_overrides_the_default(monkeypatch):
    monkeypatch.setenv("APP_TIMEOUT_S", "2.5")
    assert Settings().timeout_s == pytest.approx(2.5)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        pytest.param("APP_TIMEOUT_S", "0", id="timeout-must-be-positive"),
        pytest.param("APP_API_BASE_URL", "ftp://example.test", id="url-must-be-http"),
    ],
)
def test_invalid_value_is_rejected(monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        Settings()
