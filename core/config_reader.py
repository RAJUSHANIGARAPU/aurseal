"""
Environment-aware configuration with one precedence ladder for every key.

A value is looked up in this order, and the first non-blank answer wins:

1. ``os.environ`` under the key's environment-variable spelling
   (``base_url`` -> ``BASE_URL``, ``playwright.timeout.ms`` -> ``PLAYWRIGHT_TIMEOUT_MS``);
2. the git-ignored ``.env.<env>`` file, under the same spelling (``core.env_file``);
3. the committed ``config/environments.json`` entry for the active environment;
4. the caller's default.

Blank counts as absent at every rung. That is not a nicety: CI systems export a
parameter nobody filled in as an empty string, and an empty ``AUTH_MODE`` must not
shadow the ``auth_mode`` the environment file sets.

The environment itself resolves as: explicit argument (the ``--env`` option), then
``TEST_ENV``, then ``qa``. An unknown environment fails at construction and lists
the ones that exist, rather than surfacing later as a ``KeyError`` on ``base_url``.

The three sources are injectable (``ConfigSources``) so the ladder can be tested
without touching the real process environment. A test that reimplemented the order
would pass against itself; injecting the sources keeps the order here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from core.env_file import load_env_file
from core.logger import logger

DEFAULT_ENV = "qa"
ENV_VARIABLE = "TEST_ENV"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENVIRONMENTS_FILE = _REPO_ROOT / "config" / "environments.json"
_MISSING = object()
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def env_var_name(key: str) -> str:
    """The environment-variable spelling of a config key."""
    return "".join(c if c.isalnum() else "_" for c in key).upper()


def resolve_env(explicit: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """Active environment: explicit value, then ``TEST_ENV``, then ``qa``."""
    environ = os.environ if environ is None else environ
    for candidate in (explicit, environ.get(ENV_VARIABLE)):
        if candidate and candidate.strip():
            return candidate.strip()
    return DEFAULT_ENV


@dataclass(frozen=True)
class ConfigSources:
    """The three places a value can come from, highest precedence first."""

    os_env: Mapping[str, str]
    env_file: Mapping[str, str] = field(default_factory=dict)
    file_config: Mapping[str, object] = field(default_factory=dict)


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


class ConfigReader:
    """
    Configuration for one environment.

    ``ConfigReader("qa")`` keeps working exactly as before; ``get_base_url`` now
    also honours a ``BASE_URL`` override.
    """

    def __init__(
        self,
        env: str | None = None,
        *,
        sources: ConfigSources | None = None,
        environments_file: str | Path | None = None,
        env_file_dir: str | Path | None = None,
    ) -> None:
        self.env = resolve_env(env, None if sources is None else sources.os_env)
        # Where the git-ignored per-environment files live (``.env.<env>``, and the user
        # pool next to it); None when the sources were injected.
        self.env_file_dir: Path | None = None
        if sources is None:
            self.env_file_dir = Path(env_file_dir or _REPO_ROOT)
            sources = ConfigSources(
                os_env=os.environ,
                env_file=load_env_file(self.env, self.env_file_dir),
                file_config=self._load_environment(environments_file or _ENVIRONMENTS_FILE),
            )
        self._sources = sources

    @property
    def config(self) -> Mapping[str, object]:
        """The committed entry for the active environment (kept for older callers)."""
        return self._sources.file_config

    def _load_environment(self, path: str | Path) -> Mapping[str, object]:
        with open(path, encoding="utf-8") as handle:
            environments = json.load(handle)
        if self.env not in environments:
            raise ValueError(
                f"Unknown env '{self.env}': no entry in {Path(path).name}. "
                f"Available envs: {sorted(environments)}. "
                f"Pass one with --env=<env> or {ENV_VARIABLE}."
            )
        return environments[self.env]

    def source_of(self, key: str) -> str | None:
        """Which rung answered for ``key`` (``env``, ``env_file``, ``file``), or None."""
        name = env_var_name(key)
        if not _blank(self._sources.os_env.get(name)):
            return "env"
        if not _blank(self._sources.env_file.get(name)):
            return "env_file"
        if not _blank(self._sources.file_config.get(key)):
            return "file"
        return None

    def get(self, key: str, default: object = _MISSING) -> object:
        """The value for ``key`` from the highest rung that has one."""
        name = env_var_name(key)
        for value in (
            self._sources.os_env.get(name),
            self._sources.env_file.get(name),
            self._sources.file_config.get(key),
        ):
            if not _blank(value):
                return value.strip() if isinstance(value, str) else value
        if default is _MISSING:
            raise KeyError(f"Missing config key: {key} (env={self.env}, or set {name})")
        return default

    def get_str(self, key: str, default: str | None = None) -> str | None:
        value = self.get(key, default)
        return None if value is None else str(value)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        logger.warning("config %s=%r is not a boolean, using %s", key, value, default)
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, default)
        try:
            return int(str(value).strip())
        except ValueError:
            logger.warning("config %s=%r is not an integer, using %s", key, value, default)
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, default)
        try:
            return float(str(value).strip())
        except ValueError:
            logger.warning("config %s=%r is not a number, using %s", key, value, default)
            return default

    def get_base_url(self) -> str:
        return str(self.get("base_url"))
