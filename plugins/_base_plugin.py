"""Base plugin infrastructure — ABC, PluginResult, PluginPriority."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PluginPriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    BACKGROUND = "BACKGROUND"


@dataclass
class PluginResult:
    status: str
    findings: list[dict] = field(default_factory=list)
    duration_ms: float = 0.0
    tokens_used: int = 0
    cost_usd: float = 0.0
    dry_run: bool = False


class BasePlugin(ABC):
    """Abstract base for all framework plugins."""

    name: str = ""
    priority: PluginPriority = PluginPriority.NORMAL
    trigger_conditions: list[str] = field(default_factory=list)

    def execute(self, context: dict) -> PluginResult:
        """Run plugin with 3 retries + exponential backoff; record audit entry."""
        start = time.monotonic()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result = self.run(context)
                result.duration_ms = (time.monotonic() - start) * 1000.0
                logger.info(
                    "[PLUGIN] %s | status=%s | attempt=%d | duration_ms=%.1f",
                    self.name,
                    result.status,
                    attempt + 1,
                    result.duration_ms,
                )
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                wait = 2**attempt
                logger.warning(
                    "[PLUGIN] %s attempt %d failed: %s — retrying in %ds",
                    self.name,
                    attempt + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)

        duration_ms = (time.monotonic() - start) * 1000.0
        logger.error("[PLUGIN] %s exhausted retries: %s", self.name, last_exc)
        return PluginResult(
            status="error",
            findings=[{"error": str(last_exc)}],
            duration_ms=duration_ms,
        )

    def dry_run(self, context: dict) -> PluginResult:
        """Execute with dry_run=True injected into context."""
        return self.run({**context, "dry_run": True})

    @abstractmethod
    def run(self, context: dict) -> PluginResult:
        """Subclasses implement actual plugin logic here."""
