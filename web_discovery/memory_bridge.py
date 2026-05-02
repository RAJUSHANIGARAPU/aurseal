"""Optional bridge to the existing MemPalace memory layer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web_discovery.scenario_generator.models import TestScenario

logger = logging.getLogger(__name__)

_MEM_AVAILABLE = False
try:
    from memory.mem_palace import MemPalace  # type: ignore[import]

    _MEM_AVAILABLE = True
except ImportError:
    pass


class MemoryBridge:
    """Records discovered scenarios into MemPalace for selector learning."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled and _MEM_AVAILABLE
        self._palace: object | None = None
        if self._enabled:
            try:
                self._palace = MemPalace()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[memory-bridge] MemPalace init failed: %s", exc)
                self._enabled = False

    @property
    def available(self) -> bool:
        return self._enabled and self._palace is not None

    def record_scenarios(self, scenarios: list[TestScenario]) -> None:
        if not self.available:
            return
        for s in scenarios:
            self._record_one(s)

    def _record_one(self, scenario: TestScenario) -> None:
        try:
            record = {
                "source": "web_discovery",
                "scenario_id": scenario.id,
                "scenario_type": scenario.scenario_type.value,
                "page_url": scenario.page_url,
                "selectors": [step.selector for step in scenario.steps if step.selector],
                "tags": scenario.tags,
            }
            self._palace.store(record)  # type: ignore[union-attr]
            logger.debug("[memory-bridge] stored %s", scenario.id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[memory-bridge] store failed for %s: %s", scenario.id, exc)

    def suggest_selector(self, intent: str, page_url: str) -> str | None:
        """Return a previously-working selector for a given intent, if any."""
        if not self.available:
            return None
        try:
            result = self._palace.recall({"intent": intent, "page_url": page_url})  # type: ignore[union-attr]
            return result.get("selector") if isinstance(result, dict) else None
        except Exception:  # noqa: BLE001
            return None
