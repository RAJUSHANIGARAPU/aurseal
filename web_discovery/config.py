"""Configuration for the Web Discovery Engine.

All settings read from environment variables so CI can drive the pipeline
without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DiscoveryConfig:
    enabled: bool = False
    target_url: str = ""
    max_depth: int = 3
    max_pages: int = 50
    headless: bool = True
    auth_support: bool = False
    auth_username: str = ""
    auth_password: str = ""
    auth_url: str = ""
    output_dir: Path = field(default_factory=lambda: Path("tests/web_discovery/generated"))
    artifacts_dir: Path = field(default_factory=lambda: Path("reports/web_discovery"))
    llm_enabled: bool = True
    respect_robots: bool = True
    wait_after_nav_ms: int = 1500
    page_timeout_ms: int = 30_000

    @classmethod
    def from_env(cls) -> DiscoveryConfig:
        def _bool(key: str, default: bool = False) -> bool:
            v = os.environ.get(key, "")
            return v.lower() in ("true", "1", "yes") if v else default

        def _int(key: str, default: int) -> int:
            try:
                return int(os.environ.get(key, default))
            except (ValueError, TypeError):
                return default

        return cls(
            enabled=_bool("ENABLE_WEB_DISCOVERY"),
            target_url=os.environ.get("WD_TARGET_URL", ""),
            max_depth=_int("WD_MAX_DEPTH", 3),
            max_pages=_int("WD_MAX_PAGES", 50),
            headless=_bool("WD_HEADLESS", True),
            auth_support=_bool("WD_AUTH_SUPPORT"),
            auth_username=os.environ.get("WD_AUTH_USERNAME", ""),
            auth_password=os.environ.get("WD_AUTH_PASSWORD", ""),
            auth_url=os.environ.get("WD_AUTH_URL", ""),
            output_dir=Path(os.environ.get("WD_OUTPUT_DIR", "tests/web_discovery/generated")),
            artifacts_dir=Path(os.environ.get("WD_ARTIFACTS_DIR", "reports/web_discovery")),
            llm_enabled=_bool("WD_LLM_ENABLED", True),
            respect_robots=_bool("WD_RESPECT_ROBOTS", True),
            wait_after_nav_ms=_int("WD_WAIT_AFTER_NAV_MS", 1500),
            page_timeout_ms=_int("WD_PAGE_TIMEOUT_MS", 30_000),
        )
