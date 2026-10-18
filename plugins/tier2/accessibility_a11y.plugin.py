"""Accessibility plugin — a thin adapter over ``checks.a11y`` (axe-core in a real browser).

The template generator that wrote test files whose body was ``pass`` is gone, and so is the
``http://localhost`` default. What the plugin guarantees:

* no routes ⇒ ``unknown`` (no target is invented);
* axe or the browser unavailable, a route that does not load, a scan that ran no rules ⇒
  ``unknown``;
* a blocking violation (impact at or above ``min_impact``) not explained by the open-issue
  manifest, or a known one that no longer reproduces ⇒ ``fail``;
* every route scanned and nothing unexplained ⇒ ``pass``;
* dry run ⇒ ``skip``; no browser is started and nothing is written.
"""

from __future__ import annotations

import sys

from checks._browser import BrowserUnavailable
from checks._outcome import CheckOutcome
from checks._sink import JsonlSink
from checks.a11y import runner
from checks.a11y.assertions import load_known
from checks.a11y.axe import AxeUnavailable
from checks.a11y.judge import KIND, judge, record
from checks.a11y.policy import A11yPolicy
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins._check_adapter import plugin_result


def _routes(raw: object) -> list[str]:
    if not isinstance(raw, list | tuple):
        return []
    return [r.strip() for r in raw if isinstance(r, str) and r.strip().startswith("http")]


class AccessibilityA11yPlugin(BasePlugin):
    name = "accessibility-a11y"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["ui_change"]

    def run(self, context: dict) -> PluginResult:
        routes = _routes(context.get("routes"))
        if not routes:
            return plugin_result(
                CheckOutcome.unknown(KIND, "no routes were supplied: nothing scanned")
            )
        try:
            policy = A11yPolicy(
                min_impact=context.get("min_impact", "serious"),
                tags=tuple(context.get("tags") or ("wcag2a", "wcag2aa")),
                disabled_rules=tuple(context.get("disabled_rules") or ()),
            )
            manifest = load_known(context.get("open_issues"))
        except (ValueError, OSError) as exc:
            return plugin_result(CheckOutcome.error(KIND, f"bad configuration: {exc}"))
        if context.get("dry_run"):
            return plugin_result(
                CheckOutcome.skipped(KIND, "dry run: no browser started"), routes=routes
            )
        try:
            reports = runner.scan_routes(routes, policy, context.get("browser", "chromium"))
        except (AxeUnavailable, BrowserUnavailable) as exc:
            return plugin_result(CheckOutcome.unknown(KIND, str(exc)), routes=routes)
        sink = context.get("metric_sink")
        if sink is None and context.get("record_metrics"):
            sink = JsonlSink()
        if sink is not None:
            record(reports, sink, manifest)
        return plugin_result(judge(reports, policy, manifest, record_verdicts=False), routes=routes)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--route", action="append", default=[], help="repeatable URL")
    args = parser.parse_args()
    ctx: dict = {"routes": args.route}
    plugin = AccessibilityA11yPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
