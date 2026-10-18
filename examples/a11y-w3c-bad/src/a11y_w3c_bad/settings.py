"""Settings read from the environment (prefix ``APP_``) and an optional ``.env`` file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    base_url: str = Field(
        default="https://www.w3.org/WAI/demos/bad",
        description="Web application under test",
    )
    expect_timeout_ms: int = Field(default=5_000, gt=0, description="Web-first assertion timeout")
    wcag_tags: str = Field(
        default="wcag2a,wcag2aa,wcag21a,wcag21aa",
        description="Comma-separated axe rule tags to run (WCAG 2.1 AA by default)",
    )
    axe_script: str = Field(
        default="",
        description="Path to a pinned axe.min.js; empty uses the build axe-playwright-python ships",
    )

    timeout_s: float = Field(default=10.0, gt=0, description="Per-request or per-action timeout")
    log_level: str = "INFO"

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return value.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once."""
    return Settings()
