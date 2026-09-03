"""Base plugin infrastructure — ABC, PluginResult, PluginPriority.

A plugin has to EARN a pass.

Across the eighteen plugins there were thirty `status="pass"`, nine `"skip"`,
nine `"error"`, one `"warn"` — and **not one `"fail"`**. The platform was
structurally unable to report that something was wrong, and since `pass`, `skip`
and `warn` all counted toward the health score that gates a deploy, most ways of
learning nothing scored the same as verifying everything.

The recurring shape was a plugin defaulting to `pass` and then failing to reach
the code that would have changed it: an exception caught into a `pass`, an LLM
returning `""` on outage so a "no problems found" branch was taken, an empty
result set from a source directory that did not exist. In each case absence of
evidence was recorded as evidence of health.

So the vocabulary now separates three things that were one:

- ``PASS``/``WARN`` — the plugin looked and reached a verdict. Counts as passing.
- ``FAIL`` — the plugin looked and found a problem. Does not count.
- ``UNKNOWN`` — the plugin ran but could not reach a verdict: an outage, an
  empty input, a missing precondition. **Does not count as passing**, and is the
  correct answer far more often than the old code admitted.
- ``ERROR`` — the plugin itself broke. Does not count.
- ``SKIP`` — genuinely not applicable to this run. Scored neither way: it leaves
  the denominator instead of filling it. "I could not run" is ``UNKNOWN``, not
  ``SKIP`` — the distinction is whether anyone expected a verdict at all.
"""

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


class PluginStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    UNKNOWN = "unknown"
    SKIP = "skip"


#: Statuses that count toward the health score. Deliberately short.
PASSING_STATUSES = frozenset({PluginStatus.PASS.value, PluginStatus.WARN.value})

#: Statuses that are scored at all. A SKIP is excluded from both halves of the
#: fraction rather than counted as a pass, which is what used to hand a tier
#: full marks for a plugin whose directory was simply missing.
SCORED_STATUSES = frozenset(
    {
        PluginStatus.PASS.value,
        PluginStatus.WARN.value,
        PluginStatus.FAIL.value,
        PluginStatus.ERROR.value,
        PluginStatus.UNKNOWN.value,
    }
)

_KNOWN_STATUSES = SCORED_STATUSES | {PluginStatus.SKIP.value}


def is_passing(status: str) -> bool:
    """True only for a status that represents a verdict of health."""
    return status in PASSING_STATUSES


def is_scored(status: str) -> bool:
    """True if this result belongs in the health-score fraction at all."""
    return status in SCORED_STATUSES


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
    # A plain list, not `field(...)`. On a non-dataclass, `field(default_factory=list)`
    # leaves a `dataclasses.Field` object here — truthy, so `registry.validate()`
    # accepted it, and then `get_by_trigger` raised
    # `TypeError: argument of type 'Field' is not iterable`. Latent only because
    # all eighteen plugins happen to override it.
    trigger_conditions: list[str] = []

    def execute(self, context: dict) -> PluginResult:
        """Run plugin with 3 retries + exponential backoff; record audit entry."""
        start = time.monotonic()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result = self.run(context)
                result = self._vet(result)
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

    def _vet(self, result: object) -> PluginResult:
        """
        Refuse to let an unrecognisable return value be read as a verdict.

        Anything that is not a ``PluginResult``, or that carries a status
        outside the known vocabulary, becomes ``UNKNOWN``. A plugin returning
        ``None`` used to raise `AttributeError` on the next line and burn all
        three retries; a plugin inventing a status string would sail through
        scoring as "not passing" with no explanation of why.
        """
        if not isinstance(result, PluginResult):
            logger.error(
                "[PLUGIN] %s returned %s, not a PluginResult — recorded as unknown",
                self.name,
                type(result).__name__,
            )
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"error": f"plugin returned {type(result).__name__}"}],
            )
        if result.status not in _KNOWN_STATUSES:
            logger.error(
                "[PLUGIN] %s returned unknown status %r — recorded as unknown",
                self.name,
                result.status,
            )
            result.findings = [*result.findings, {"error": f"unknown status {result.status!r}"}]
            result.status = PluginStatus.UNKNOWN.value
        return result

    def dry_run(self, context: dict) -> PluginResult:
        """Execute with dry_run=True injected into context."""
        return self.run({**context, "dry_run": True})

    @abstractmethod
    def run(self, context: dict) -> PluginResult:
        """Subclasses implement actual plugin logic here."""
