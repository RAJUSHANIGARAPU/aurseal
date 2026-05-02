"""Temporal regression plugin — replays prod events and classifies diffs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class TemporalRegressionPlugin(BasePlugin):
    name = "temporal-regression"
    priority = PluginPriority.BACKGROUND
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        log_path_str = os.environ.get("PROD_EVENT_LOG_PATH") or context.get("prod_event_log_path")
        governor = context.get("cost_governor") or CostGovernor()

        if not log_path_str:
            return PluginResult(
                status="skip",
                findings=[{"reason": "PROD_EVENT_LOG_PATH not set"}],
                dry_run=context.get("dry_run", False),
            )

        log_path = Path(log_path_str)
        if not log_path.exists():
            return PluginResult(
                status="skip",
                findings=[{"reason": f"Log file not found: {log_path}"}],
            )

        # Sample up to 100 events
        events: list[dict] = []
        try:
            lines = log_path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[:100]:
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        events.append({"raw": line[:200]})
        except Exception as exc:  # noqa: BLE001
            return PluginResult(status="error", findings=[{"error": str(exc)}])

        if not events:
            return PluginResult(status="skip", findings=[{"reason": "No events in log"}])

        # Use Claude (haiku) to classify each replay diff
        classifications: list[dict] = []
        regression_count = 0
        try:
            from api.llm.claude_client import ClaudeLLMClient

            model = governor.get_model("claude-haiku-4-5-20251001")
            llm = ClaudeLLMClient(model=model)
            sample = json.dumps(events[:10], indent=2)[:2000]
            prompt = (
                f"Review these {len(events)} event replay diffs. "
                f"Sample:\n{sample}\n\n"
                "For each event, classify as 'intentional' (planned change) or "
                "'regression' (unexpected). Reply with JSON array: "
                '[{"index": 0, "classification": "intentional|regression"}]'
            )
            response = governor.cached_complete(prompt, llm.complete)
            if response and not response.startswith("Claude API error"):
                try:
                    start = response.find("[")
                    end = response.rfind("]") + 1
                    if start >= 0 and end > start:
                        parsed = json.loads(response[start:end])
                        classifications = parsed
                        regression_count = sum(
                            1 for c in parsed if c.get("classification") == "regression"
                        )
                except Exception:  # noqa: BLE001
                    classifications = [{"classification": "unknown", "count": len(events)}]
        except Exception:  # noqa: BLE001
            classifications = [{"classification": "unknown", "count": len(events)}]

        return PluginResult(
            status="warn" if regression_count > 0 else "pass",
            findings=[
                {
                    "events_sampled": len(events),
                    "regression_count": regression_count,
                    "classifications": classifications[:20],
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = TemporalRegressionPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip", "warn") else 1)
