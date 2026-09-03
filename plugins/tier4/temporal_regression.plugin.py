"""
Temporal regression plugin — replays prod events and classifies the diffs.

Two ways this used to report "no regressions" without having looked.

**The outage.** ``regression_count`` was initialised to ``0`` and only ever
raised inside a branch guarded by ``response and not response.startswith(...)``
and then by finding a ``[`` in the reply. An empty string, an error banner, a
model that answered in prose — each skipped the branch and left the counter at
zero, and the return read ``"warn" if regression_count > 0 else "pass"``. Every
way of learning nothing produced the same green as a clean replay.

**The sample.** Up to a hundred events were read from the log and exactly ten
were put in the prompt, truncated again at two thousand characters. The verdict
was then reported against all hundred. The prompt now grows until a character
budget and the events that did not fit are counted and named: a run that could
not look at everything says ``unknown`` instead of passing the part it saw off
as the whole.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.cost_governor import CostGovernor

logger = logging.getLogger(__name__)

#: Events read off the log at all.
_MAX_EVENTS = 100

#: Characters of event JSON the prompt will carry. Generous enough that an
#: ordinary log is examined in full — otherwise ``unknown`` would be the only
#: reachable verdict and the plugin would stop saying anything.
_PROMPT_BUDGET = 12_000


def _fit_to_budget(events: list[dict], budget: int) -> list[dict]:
    """The prefix of ``events`` whose JSON fits in ``budget`` characters."""
    fitted: list[dict] = []
    size = 0
    for event in events:
        chunk = json.dumps(event, ensure_ascii=False)
        if fitted and size + len(chunk) > budget:
            break
        fitted.append(event)
        size += len(chunk) + 2
    return fitted


def _parse_classifications(response: str) -> list[dict] | None:
    """The JSON array the model was asked for, or None if the reply is unusable.

    None and an empty list are different answers and the caller treats them
    differently: None is "no verdict was reached", ``[]`` is "the model replied
    and classified nothing".
    """
    if not response or response.startswith("Claude API error"):
        return None
    start = response.find("[")
    end = response.rfind("]") + 1
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(response[start:end])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(parsed, list) or not all(isinstance(c, dict) for c in parsed):
        return None
    return parsed


class TemporalRegressionPlugin(BasePlugin):
    name = "temporal-regression"
    priority = PluginPriority.BACKGROUND
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        log_path_str = os.environ.get("PROD_EVENT_LOG_PATH") or context.get("prod_event_log_path")
        governor = context.get("cost_governor") or CostGovernor()

        if not log_path_str:
            # Nobody pointed this run at a log, so nobody expected a verdict —
            # the one case here that really is "not applicable".
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{"reason": "PROD_EVENT_LOG_PATH not set"}],
                dry_run=context.get("dry_run", False),
            )

        log_path = Path(log_path_str)
        if not log_path.exists():
            # A log was named and is not there. That is a broken precondition,
            # not an inapplicable check, and it must not be excused from the score.
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": f"log file not found: {log_path}"}],
            )

        try:
            lines = [ln for ln in log_path.read_text(encoding="utf-8").split("\n") if ln.strip()]
        except Exception as exc:  # noqa: BLE001
            return PluginResult(status=PluginStatus.ERROR.value, findings=[{"error": str(exc)}])

        events: list[dict] = []
        for line in lines[:_MAX_EVENTS]:
            try:
                events.append(json.loads(line))
            except Exception:  # noqa: BLE001
                events.append({"raw": line[:200]})

        if not events:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[{"reason": f"log holds no events: {log_path}"}],
            )

        examined = _fit_to_budget(events, _PROMPT_BUDGET)
        unexamined = len(lines) - len(examined)

        classifications = self._classify(examined, governor)
        coverage = {
            "events_in_log": len(lines),
            "events_examined": len(examined),
            "events_unexamined": unexamined,
        }

        if classifications is None:
            return PluginResult(
                status=PluginStatus.UNKNOWN.value,
                findings=[
                    {
                        "reason": "the model returned no usable classification",
                        **coverage,
                    }
                ],
            )

        regressions = [c for c in classifications if c.get("classification") == "regression"]

        if regressions:
            status = PluginStatus.FAIL.value
        elif unexamined:
            # Nothing wrong in what was read, and part of the log was never
            # read. "No regressions" would be a claim about events this run
            # never saw.
            status = PluginStatus.UNKNOWN.value
        else:
            status = PluginStatus.PASS.value

        return PluginResult(
            status=status,
            findings=[
                {
                    **coverage,
                    "regression_count": len(regressions),
                    "classifications": classifications[:20],
                }
            ],
        )

    def _classify(self, events: list[dict], governor: CostGovernor) -> list[dict] | None:
        """One model call over the events that fit. None means no verdict."""
        try:
            from api.llm.claude_client import ClaudeLLMClient

            llm = ClaudeLLMClient(model=governor.get_model("claude-haiku-4-5-20251001"))
            sample = json.dumps(events, indent=2)[:_PROMPT_BUDGET]
            prompt = (
                f"Review these {len(events)} event replay diffs. "
                f"Events:\n{sample}\n\n"
                "For each event, classify as 'intentional' (planned change) or "
                "'regression' (unexpected). Reply with JSON array: "
                '[{"index": 0, "classification": "intentional|regression"}]'
            )
            return _parse_classifications(governor.cached_complete(prompt, llm.complete))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] classification unavailable: %s", self.name, exc)
            return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = TemporalRegressionPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "warn", "skip") else 1)
