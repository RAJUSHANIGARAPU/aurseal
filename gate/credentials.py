"""Per-environment login credentials, checked before any test starts.

Each target environment names the variables its login comes from in
``config/environments.json``::

    "credentials": {"username_var": "LOGIN_USERNAME", "password_var": "LOGIN_PASSWORD",
                    "shared": false}

In GitHub Actions every target is a GitHub Environment holding the same secret names, and
the job selects ``environment: ${{ inputs.env || 'qa' }}``. A repository-level fallback
(``SHARED_LOGIN_USERNAME`` / ``SHARED_LOGIN_PASSWORD``) is used only by an environment marked
``"shared": true``; an environment that must have its own credential fails instead of
silently logging in as the shared user.

``python -m gate.credentials preflight --env <name>`` runs as the first step after install
in every job that logs in:

* exit 0 when the values the environment's auth mode needs are present (an environment
  whose auth mode is ``none`` needs nothing), and the resolved values are exported to
  ``$GITHUB_ENV`` under the names the auth layer reads (``AUTH_USERNAME``,
  ``AUTH_PASSWORD``, ``SUPPLIED_TOKEN``);
* exit 1 when anything is missing, naming the environment, the missing variable names and
  the GitHub Environment to set them in; exit 2 for an unknown environment.

No value is ever printed. Values from the ``secrets`` context are masked by the runner.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from core.auth.secrets import lookup_secret

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS_FILE = ROOT / "config" / "environments.json"
SHARED_USERNAME_VAR = "SHARED_LOGIN_USERNAME"
SHARED_PASSWORD_VAR = "SHARED_LOGIN_PASSWORD"
TOKEN_VAR = "SUPPLIED_TOKEN"
LOGIN_MODES = frozenset({"ui", "password"})
EXIT_OK, EXIT_MISSING, EXIT_UNKNOWN = 0, 1, 2


@dataclass(frozen=True)
class CredentialSource:
    env: str
    auth_mode: str
    username_var: str = "LOGIN_USERNAME"
    password_var: str = "LOGIN_PASSWORD"
    shared: bool = False


@dataclass(frozen=True)
class Preflight:
    code: int
    message: str
    exports: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return self.code == EXIT_OK


def load_environments(path: str | Path = ENVIRONMENTS_FILE) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def source_for(
    env: str, environments: Mapping[str, Mapping], environ: Mapping[str, str]
) -> CredentialSource:
    block = environments[env]
    creds = block.get("credentials") or {}
    mode = (environ.get("AUTH_MODE") or "").strip().lower() or str(block.get("auth_mode", "none"))
    return CredentialSource(
        env=env,
        auth_mode=mode.replace("-", "_"),
        username_var=str(creds.get("username_var", "LOGIN_USERNAME")),
        password_var=str(creds.get("password_var", "LOGIN_PASSWORD")),
        shared=bool(creds.get("shared", False)),
    )


def _value(name: str, environ: Mapping[str, str]) -> str | None:
    # Environment only in CI; locally the OS credential store is the second rung.
    return lookup_secret(name, environ=environ, keyring_module=_NoKeyring())


class _NoKeyring:
    @staticmethod
    def get_password(service: str, name: str) -> None:
        return None


def preflight(
    env: str,
    environ: Mapping[str, str] | None = None,
    environments: Mapping[str, Mapping] | None = None,
) -> Preflight:
    environ = os.environ if environ is None else environ
    environments = load_environments() if environments is None else environments
    if env not in environments:
        return Preflight(
            EXIT_UNKNOWN, f"unknown environment {env!r}; known: {sorted(environments)}"
        )
    source = source_for(env, environments, environ)
    where = f"set them as secrets/variables of the GitHub Environment {env!r}"
    if source.auth_mode == "none":
        return Preflight(EXIT_OK, f"credentials for {env!r}: auth mode none, nothing needed")
    if source.auth_mode == "token":
        if _value(TOKEN_VAR, environ):
            return Preflight(EXIT_OK, f"credentials for {env!r}: supplied token present")
        return Preflight(
            EXIT_MISSING, f"credentials for {env!r}: auth mode token needs {TOKEN_VAR}; {where}"
        )
    own = {
        source.username_var: _value(source.username_var, environ),
        source.password_var: _value(source.password_var, environ),
    }
    missing = [name for name, value in own.items() if not value]
    if not missing:
        return Preflight(
            EXIT_OK,
            f"credentials for {env!r}: {source.username_var}, {source.password_var} present",
            {"AUTH_USERNAME": own[source.username_var], "AUTH_PASSWORD": own[source.password_var]},
        )
    fallback = {
        SHARED_USERNAME_VAR: _value(SHARED_USERNAME_VAR, environ),
        SHARED_PASSWORD_VAR: _value(SHARED_PASSWORD_VAR, environ),
    }
    fallback_complete = all(fallback.values())
    if source.shared and fallback_complete:
        return Preflight(
            EXIT_OK,
            f"credentials for {env!r}: using the shared {SHARED_USERNAME_VAR} login",
            {
                "AUTH_USERNAME": fallback[SHARED_USERNAME_VAR],
                "AUTH_PASSWORD": fallback[SHARED_PASSWORD_VAR],
            },
        )
    note = ""
    if fallback_complete and not source.shared:
        note = (
            f" (a shared login is set, but {env!r} is marked shared: false and must have its own)"
        )
    return Preflight(
        EXIT_MISSING,
        f"credentials for {env!r} (auth mode {source.auth_mode}): missing "
        f"{', '.join(missing)}{note}; {where}",
    )


def write_exports(path: str | Path, exports: Mapping[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in exports.items():
            delimiter = f"EOF_{secrets.token_hex(8)}"
            handle.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="python -m gate.credentials")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="check the chosen environment's login is configured")
    pre.add_argument("--env", default=environ.get("TEST_ENV") or "qa")
    pre.add_argument("--environments", default=str(ENVIRONMENTS_FILE))
    pre.add_argument("--github-env", default=environ.get("GITHUB_ENV"))
    args = parser.parse_args(argv)
    result = preflight(args.env, environ, load_environments(args.environments))
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream)
    if result.ok and result.exports and args.github_env:
        write_exports(args.github_env, result.exports)
    return result.code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
