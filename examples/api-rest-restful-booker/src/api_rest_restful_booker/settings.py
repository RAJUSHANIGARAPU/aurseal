"""Settings read from the environment (prefix ``APP_``) and an optional ``.env`` file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    api_base_url: str = Field(
        default="https://restful-booker.herokuapp.com",
        description="Base URL of the API under test",
    )
    max_retries: int = Field(default=2, ge=0, description="Extra attempts for a failed GET")
    # The admin account the API's own documentation publishes for everyone to use.
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("password123")

    timeout_s: float = Field(default=10.0, gt=0, description="Per-request or per-action timeout")
    log_level: str = "INFO"

    @field_validator("api_base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return value.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once."""
    return Settings()
