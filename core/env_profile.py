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

``business_timezone`` is optional and defaults to ``UTC``: the zone the service
under test computes its calendar days in (:mod:`core.clock`). An unknown zone
name fails here rather than as an off-by-one-day date much later.

Every value still goes through ``ConfigReader``, so ``TIMEOUT_MS=60000`` in the
shell or in ``.env.<env>`` overrides the committed value exactly like any other
key.

One optional key sits outside the contract: ``browser_timezone`` (an IANA name such
as ``Europe/Lisbon``; ``BROWSER_TIMEZONE``) pins the browser context's time zone, so
a date the UI renders from an API timestamp is the date the API means rather than
whatever zone the machine running the browser is in. Absent, the browser keeps the
machine's zone, as before. An unknown zone name fails here instead of in Playwright.
"""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.clock import CONFIG_KEY as BUSINESS_TIMEZONE_KEY
from core.clock import DEFAULT_ZONE, zone_named
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
    business_timezone: str = DEFAULT_ZONE
    browser_timezone: str | None = None

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
        zone = str(config.get(BUSINESS_TIMEZONE_KEY, DEFAULT_ZONE)).strip() or DEFAULT_ZONE
        zone_named(zone)
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
            business_timezone=zone,
            browser_timezone=browser_timezone(config),
        )

    def playwright_launch_args(self) -> dict:
        """The launch keyword arguments this profile implies."""
        return {"headless": self.headless, "slow_mo": self.slow_mo_ms}

    def context_args(self) -> dict:
        """The browser context keyword arguments this profile implies."""
        return timezone_context_args(self.browser_timezone)


def browser_timezone(config: ConfigReader) -> str | None:
    """The configured browser time zone, validated; None when not configured."""
    value = config.get_str("browser_timezone", None)
    if value is None or not value.strip():
        return None
    name = value.strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"browser_timezone={name!r} for env {config.env} is not a known IANA time zone"
        ) from None
    return name


def timezone_context_args(timezone_id: str | None) -> dict:
    """``{"timezone_id": ...}`` for ``browser.new_context``; ``{}`` leaves the default."""
    return {"timezone_id": timezone_id} if timezone_id else {}


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
