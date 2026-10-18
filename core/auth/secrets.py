"""
Finding a secret without committing it or typing it into a shell.

The lookup order is fixed:

1. the environment variable (which, through the config ladder, also covers a
   git-ignored ``.env.<env>`` file the caller already resolved);
2. the operating system's credential store, through the optional ``keyring``
   package (macOS Keychain, Windows Credential Locker, Secret Service on Linux).

``keyring`` is imported lazily and only when the first rung is empty, so a CI run
that exports its secrets never needs the package. When it is needed but absent,
the error says so and says what to set instead — never a bare ``ImportError``
from deep inside a token request.

Store a secret once with ``keyring set <service> <NAME>``; the default service is
``test-automation`` and ``AUTH_KEYRING_SERVICE`` overrides it.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping

DEFAULT_KEYRING_SERVICE = "test-automation"
KEYRING_SERVICE_VARIABLE = "AUTH_KEYRING_SERVICE"


class SecretNotFound(LookupError):
    """No rung had the secret; the message names every place that was tried."""


def keyring_service(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    return (environ.get(KEYRING_SERVICE_VARIABLE) or "").strip() or DEFAULT_KEYRING_SERVICE


def _load_keyring():
    try:
        return importlib.import_module("keyring")
    except ImportError:
        return None


def lookup_secret(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    service: str | None = None,
    keyring_module: object | None = None,
) -> str | None:
    """
    The secret called ``name``, or None when no rung has it.

    ``keyring_module`` is injectable for tests; by default ``keyring`` is
    imported on first need. A credential-store error (locked keychain, no
    backend) counts as "not found" here — ``require_secret`` reports it.
    """
    environ = os.environ if environ is None else environ
    value = (environ.get(name) or "").strip()
    if value:
        return value
    module = keyring_module if keyring_module is not None else _load_keyring()
    if module is None:
        return None
    try:
        stored = module.get_password(service or keyring_service(environ), name)
    except Exception:  # noqa: BLE001 - every backend raises its own error type
        return None
    return stored.strip() if isinstance(stored, str) and stored.strip() else None


def require_secret(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    service: str | None = None,
    keyring_module: object | None = None,
) -> str:
    """Like ``lookup_secret`` but raises ``SecretNotFound`` explaining where to put it."""
    found = lookup_secret(name, environ=environ, service=service, keyring_module=keyring_module)
    if found:
        return found
    service = service or keyring_service(environ)
    has_keyring = keyring_module is not None or _load_keyring() is not None
    store = (
        f"or store it with `keyring set {service} {name}`"
        if has_keyring
        else "or install the optional `keyring` package and store it there"
    )
    raise SecretNotFound(f"secret {name} not found: set the {name} environment variable {store}")
