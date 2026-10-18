"""
The per-environment run profile: everything ``config/environments.json`` defines
for one environment beyond its ``base_url``, read through the config ladder.

Each environment in the committed file carries the same keys — the API base URL,
browser defaults, timeouts, artifact toggles, the AI model and its timeout, and
the read-only and assertion switches. Keeping the key set identical across
environments is what makes "it works on qa" mean something on staging: a key
that exists for one environment only is a default the other one silently
inherits from code. ``REQUIRED_KEYS`` is that contract, and the offline test
over the real file holds every environment to it.

Every value still goes through ``ConfigReader``, so ``TIMEOUT_MS=60000`` in the
shell or in ``.env.<env>`` overrides the committed value exactly like any other
key.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config_reader import ConfigReader

ARTIFACT_MODES = ("on", "off", "retain-on-failure")

REQUIRED_KEYS = (
    "base_url",
    "api_base_url",
    "browser",
    "headless",
    "timeout_ms",
    "slow_mo_ms",
    "video",
    "tracing",
    "ai_model",
    "ai_timeout_s",
    "auth_mode",
    "read_only",
    "api_assertions_enabled",
)

_BROWSERS = ("chromium", "firefox", "webkit")


@dataclass(frozen=True)
class EnvProfile:
    env: str
    base_url: str
    api_base_url: str
    browser: str
    headless: bool
    timeout_ms: int
    slow_mo_ms: int
    video: str
    tracing: str
    ai_model: str
    ai_timeout_s: int
    read_only: bool
    api_assertions_enabled: bool

    @classmethod
    def from_config(cls, config: ConfigReader) -> EnvProfile:
        """
        The profile for ``config.env``.

        Raises ``KeyError`` for a missing key rather than inventing a default, and
        ``ValueError`` for a browser or artifact mode no tool would accept.
        """
        browser = str(config.get("browser")).lower()
        if browser not in _BROWSERS:
            raise ValueError(f"browser={browser!r} for env {config.env}; expected {_BROWSERS}")
        video = _artifact_mode(config, "video")
        tracing = _artifact_mode(config, "tracing")
        return cls(
            env=config.env,
            base_url=str(config.get("base_url")),
            api_base_url=str(config.get("api_base_url")),
            browser=browser,
            headless=_bool(config, "headless"),
            timeout_ms=_int(config, "timeout_ms"),
            slow_mo_ms=_int(config, "slow_mo_ms"),
            video=video,
            tracing=tracing,
            ai_model=str(config.get("ai_model")),
            ai_timeout_s=_int(config, "ai_timeout_s"),
            read_only=_bool(config, "read_only"),
            api_assertions_enabled=_bool(config, "api_assertions_enabled"),
        )

    def playwright_launch_args(self) -> dict:
        """The launch keyword arguments this profile implies."""
        return {"headless": self.headless, "slow_mo": self.slow_mo_ms}


def missing_keys(entry: dict) -> list[str]:
    """``REQUIRED_KEYS`` absent from one environment entry, in contract order."""
    return [key for key in REQUIRED_KEYS if key not in entry]


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _bool(config: ConfigReader, key: str) -> bool:
    # Strict on purpose: the typed getters fall back to a default on a typo, and a
    # profile with no default must not quietly turn ``headless=ture`` into False.
    value = config.get(key)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{key}={value!r} for env {config.env} is not a boolean")


def _int(config: ConfigReader, key: str) -> int:
    value = config.get(key)
    try:
        return int(str(value).strip())
    except ValueError:
        raise ValueError(f"{key}={value!r} for env {config.env} is not an integer") from None


def _artifact_mode(config: ConfigReader, key: str) -> str:
    value = str(config.get(key)).lower()
    if value not in ARTIFACT_MODES:
        raise ValueError(f"{key}={value!r} for env {config.env}; expected {ARTIFACT_MODES}")
    return value
