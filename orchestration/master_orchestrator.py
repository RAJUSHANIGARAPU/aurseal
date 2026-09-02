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

        # Fire deploy webhook if set. A dry run is documented as having no side
        # effects, so it must not reach the real deploy hook — this block used
        # to post regardless, which made the README's own --dry-run example
        # notify a deploy.
        webhook_url = os.environ.get("DEPLOY_WEBHOOK_URL")
        if webhook_url and context.get("dry_run"):
            self._logger.log("orchestrator", "webhook_skipped", {"reason": "dry_run"})
        elif webhook_url:
            self._notify_deploy(webhook_url, run_id, health_score, deploy)

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

    def _notify_deploy(
        self,
        webhook_url: str,
        run_id: str,
        health_score: int,
        deploy: bool,
    ) -> None:
        """POST the run verdict to the deploy webhook.

        Delivery is best-effort — a webhook that is down must not fail the run —
        but it is never silent. The previous ``except Exception: pass`` left no
        trace anywhere of a hook that was unreachable, misconfigured or
        rejecting the payload, so a deploy that was never notified looked
        exactly like one that was.
        """
        try:
            import requests

            response = requests.post(
                webhook_url,
                json={"run_id": run_id, "health_score": health_score, "deploy": deploy},
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.log("orchestrator", "webhook_error", {"error": str(exc)})
            return

        # A rejected payload is a delivery failure too; it just does not raise.
        if response.status_code >= 400:
            self._logger.log(
                "orchestrator", "webhook_failed", {"status_code": response.status_code}
            )

    def _compute_health(
        self,
        results: dict[str, PluginResult],
        by_priority: dict[PluginPriority, list],
    ) -> int:
        """Compute weighted health score: CRITICAL 40%, HIGH 35%, NORMAL 25%.

        Scored against the plugins that were expected to run, not the ones that
        reported back. A plugin missing from ``results`` counts as not passing:
        it may have failed to import (PluginRegistry logs that and carries on),
        died in its worker, or sat behind a CRITICAL failure that stopped the
        tier — in every case the run learned nothing about it, and nothing
        learned is not a pass.

        Scoring only the reporters is what made a broken run look perfect: one
        unimportable dependency dropped its plugins from the denominator, and
        with every plugin gone the score was 100 and the deploy went green —
        a better result than an honest run where everything fails and scores 0.
        """
        total_weight = 0.0
        weighted_score = 0.0

        for priority, weight in _WEIGHTS.items():
            expected = by_priority.get(priority, [])
            if not expected:
                continue
            passing = sum(
                1
                for p in expected
                if p.name in results and results[p.name].status in ("pass", "skip", "warn")
            )
            weighted_score += weight * (passing / len(expected))
            total_weight += weight

        if total_weight == 0:
            # Nothing carrying weight was even expected, so the run holds no
            # evidence at all. It must not clear the deploy threshold.
            return 0
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
