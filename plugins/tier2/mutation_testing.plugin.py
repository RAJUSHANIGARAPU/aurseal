"""Mutation testing plugin — a thin adapter over ``checks.mutation`` (mutmut 3.x).

The old plugin called mutmut with 2.x flags (``--paths-to-mutate``,
``--disable-mutation-types``) that mutmut 3 does not have, took the first integer on any
line mentioning "survived" as the survivor count, reported survivors as ``warn`` (which
counts as passing), a missing mutmut as ``skip`` and a dry run as ``pass``. Now:

* scope and per-package thresholds come from ``checks/mutation/mutation.toml``; mutmut 3 is
  configured through a generated ``[tool.mutmut]`` table in an isolated copy of the repo;
* counts come from mutmut's per-mutant listing, cross-checked with its own totals;
* every selected package at or above its threshold ⇒ ``pass``; any below ⇒ ``fail`` naming
  the package and surviving mutants;
* mutmut missing, its run failing, nothing generated, mutants left unchecked ⇒ ``unknown``;
* a bad configuration ⇒ ``error``; dry run ⇒ ``skip`` (nothing started).
"""

from __future__ import annotations

import sys

from checks._outcome import CheckOutcome
from checks._sink import JsonlSink
from checks.mutation import runner
from checks.mutation.config import DEFAULT_CONFIG, MutationConfig
from checks.mutation.judge import KIND
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins._check_adapter import plugin_result


class MutationTestingPlugin(BasePlugin):
    name = "mutation-testing"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        try:
            config = MutationConfig.from_pyproject(context.get("config") or DEFAULT_CONFIG)
            scopes = config.select(context.get("packages"))
        except ValueError as exc:
            return plugin_result(CheckOutcome.error(KIND, f"configuration rejected: {exc}"))
        packages = [s.name for s in scopes]
        if context.get("dry_run"):
            return plugin_result(
                CheckOutcome.skipped(KIND, "dry run: mutmut not started"), packages=packages
            )
        sink = context.get("metric_sink")
        if sink is None and context.get("record_metrics"):
            sink = JsonlSink()
        outcome = runner.run_mutation(config, packages, sink=sink)
        return plugin_result(outcome, packages=packages)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--package", action="append", default=None)
    args = parser.parse_args()
    ctx: dict = {"packages": args.package}
    plugin = MutationTestingPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
