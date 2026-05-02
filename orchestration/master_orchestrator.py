"""Master orchestrator — runs plugins by priority tier and computes health score."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Thread

from api.engine.observability import AgentLogger
from orchestration.storage import PluginStorage
from plugins._base_plugin import PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor
from plugins.registry import PluginRegistry

_WEIGHTS = {
    PluginPriority.CRITICAL: 0.40,
    PluginPriority.HIGH: 0.35,
    PluginPriority.NORMAL: 0.25,
    PluginPriority.BACKGROUND: 0.0,
}


def _run_plugin(plugin, context: dict) -> tuple[str, PluginResult]:
    result = plugin.execute(context)
    return plugin.name, result


class MasterOrchestrator:
    """Orchestrates plugin execution across priority tiers."""

    def __init__(self, budget_usd: float = 5.0) -> None:
        self._governor = CostGovernor(budget_total=budget_usd)
        self._registry = PluginRegistry()
        self._registry.scan()
        self._storage = PluginStorage()
        session_id = f"orchestrator_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._logger = AgentLogger(session_id=session_id)

    def run(self, context: dict) -> dict:
        run_id = str(uuid.uuid4())
        trigger = context.get("trigger", "manual")
        ctx = {**context, "cost_governor": self._governor}

        self._logger.log("orchestrator", "run_start", {"run_id": run_id, "trigger": trigger})

        plugins = self._registry.get_by_trigger(trigger)
        if not plugins:
            plugins = self._registry.all()

        by_priority: dict[PluginPriority, list] = {p: [] for p in PluginPriority}
        for plugin in plugins:
            by_priority[plugin.priority].append(plugin)

        results: dict[str, PluginResult] = {}

        # CRITICAL — sequential, stop on first failure
        for plugin in by_priority[PluginPriority.CRITICAL]:
            self._logger.log(
                "orchestrator", "plugin_start", {"plugin": plugin.name, "priority": "CRITICAL"}
            )
            name, result = _run_plugin(plugin, ctx)
            results[name] = result
            self._logger.log(
                "orchestrator", "plugin_done", {"plugin": name, "status": result.status}
            )
            if result.status == "fail":
                self._logger.log("orchestrator", "critical_failure", {"plugin": name})
                break

        # HIGH — parallel
        high_plugins = by_priority[PluginPriority.HIGH]
        if high_plugins:
            with ThreadPoolExecutor(max_workers=min(4, len(high_plugins))) as executor:
                futures = {executor.submit(_run_plugin, p, ctx): p for p in high_plugins}
                for future in as_completed(futures):
                    try:
                        name, result = future.result()
                        results[name] = result
                        self._logger.log(
                            "orchestrator", "plugin_done", {"plugin": name, "status": result.status}
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._logger.log("orchestrator", "plugin_error", {"error": str(exc)})

        # NORMAL — parallel
        normal_plugins = by_priority[PluginPriority.NORMAL]
        if normal_plugins:
            with ThreadPoolExecutor(max_workers=min(8, len(normal_plugins))) as executor:
                futures = {executor.submit(_run_plugin, p, ctx): p for p in normal_plugins}
                for future in as_completed(futures):
                    try:
                        name, result = future.result()
                        results[name] = result
                        self._logger.log(
                            "orchestrator", "plugin_done", {"plugin": name, "status": result.status}
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._logger.log("orchestrator", "plugin_error", {"error": str(exc)})

        # BACKGROUND — fire and forget daemon threads
        for plugin in by_priority[PluginPriority.BACKGROUND]:
            t = Thread(target=_run_plugin, args=(plugin, ctx), daemon=True)
            t.start()
            self._logger.log("orchestrator", "background_fired", {"plugin": plugin.name})

        # Compute health score
        health_score = self._compute_health(results, by_priority)
        deploy = health_score >= 70
        self._logger.log("orchestrator", "health_score", {"score": health_score, "deploy": deploy})

        # Fire deploy webhook if set
        webhook_url = os.environ.get("DEPLOY_WEBHOOK_URL")
        if webhook_url:
            try:
                import requests

                requests.post(
                    webhook_url,
                    json={"run_id": run_id, "health_score": health_score, "deploy": deploy},
                    timeout=10,
                )
            except Exception:  # noqa: BLE001
                pass

        # Persist run
        plugins_run = list(results.keys())
        total_cost = sum(r.cost_usd for r in results.values())
        self._storage.save_run(run_id, health_score, plugins_run, total_cost)
        for name, result in results.items():
            self._storage.save_plugin_result(run_id, name, result)

        summary = {
            "plugins_run": plugins_run,
            "statuses": {name: r.status for name, r in results.items()},
            "total_cost_usd": total_cost,
        }
        self._logger.log(
            "orchestrator", "run_complete", {"run_id": run_id, "health_score": health_score}
        )

        return {
            "health_score": health_score,
            "deploy": deploy,
            "run_id": run_id,
            "summary": summary,
        }

    def _compute_health(
        self,
        results: dict[str, PluginResult],
        by_priority: dict[PluginPriority, list],
    ) -> int:
        """Compute weighted health score: CRITICAL 40%, HIGH 35%, NORMAL 25%."""
        total_weight = 0.0
        weighted_score = 0.0

        for priority, weight in _WEIGHTS.items():
            plugins_in_tier = by_priority.get(priority, [])
            run_in_tier = [p for p in plugins_in_tier if p.name in results]
            if not run_in_tier:
                continue
            passing = sum(
                1 for p in run_in_tier if results[p.name].status in ("pass", "skip", "warn")
            )
            tier_score = passing / len(run_in_tier)
            weighted_score += weight * tier_score
            total_weight += weight

        if total_weight == 0:
            return 100  # no plugins run
        return round((weighted_score / total_weight) * 100)


def main() -> int:
    parser = argparse.ArgumentParser(description="Master plugin orchestrator")
    parser.add_argument("--trigger", default="manual", help="trigger event name")
    parser.add_argument("--budget", type=float, default=5.0, help="budget in USD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    orchestrator = MasterOrchestrator(budget_usd=args.budget)
    result = orchestrator.run({"trigger": args.trigger, "dry_run": args.dry_run})
    print(
        f"health_score={result['health_score']} deploy={result['deploy']}"
        f" run_id={result['run_id']}"
    )
    return 0 if result["deploy"] else 1


if __name__ == "__main__":
    sys.exit(main())
