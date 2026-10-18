"""Secret lookup: environment first, then the optional OS credential store."""

from __future__ import annotations

import sys
import types

import pytest

from core.auth.secrets import (
    DEFAULT_KEYRING_SERVICE,
    SecretNotFound,
    keyring_service,
    lookup_secret,
    require_secret,
)


class _FakeKeyring:
    def __init__(self, store=None, error=None):
        self.store = store or {}
        self.error = error
        self.calls = []

    def get_password(self, service, name):
        self.calls.append((service, name))
        if self.error:
            raise self.error
        return self.store.get((service, name))


def test_environment_wins_and_the_store_is_not_consulted():
    store = _FakeKeyring({(DEFAULT_KEYRING_SERVICE, "API_SECRET"): "from-store"})
    assert lookup_secret("API_SECRET", environ={"API_SECRET": " env "}, keyring_module=store) == (
        "env"
    )
    assert store.calls == []


def test_blank_environment_falls_through_to_the_store():
    store = _FakeKeyring({(DEFAULT_KEYRING_SERVICE, "API_SECRET"): "from-store"})
    assert lookup_secret("API_SECRET", environ={"API_SECRET": " "}, keyring_module=store) == (
        "from-store"
    )


def test_service_name_is_configurable():
    store = _FakeKeyring({("my-suite", "API_SECRET"): "x"})
    environ = {"AUTH_KEYRING_SERVICE": "my-suite"}
    assert keyring_service(environ) == "my-suite"
    assert lookup_secret("API_SECRET", environ=environ, keyring_module=store) == "x"


def test_a_store_error_counts_as_not_found():
    store = _FakeKeyring(error=RuntimeError("keychain locked"))
    assert lookup_secret("API_SECRET", environ={}, keyring_module=store) is None


def test_keyring_is_imported_lazily(monkeypatch):
    fake = types.ModuleType("keyring")
    fake.get_password = lambda service, name: "lazy"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert lookup_secret("API_SECRET", environ={}) == "lazy"


def test_absent_package_gives_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)  # makes the import fail
    assert lookup_secret("API_SECRET", environ={}) is None
    with pytest.raises(SecretNotFound, match="install the optional `keyring` package"):
        require_secret("API_SECRET", environ={})


def test_present_store_without_the_secret_says_how_to_store_it():
    with pytest.raises(SecretNotFound, match="keyring set test-automation API_SECRET"):
        require_secret("API_SECRET", environ={}, keyring_module=_FakeKeyring())
