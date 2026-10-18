"""Visual regression plugin — a thin adapter over ``checks.visual``.

The old plugin wrote a 0-byte "baseline" for every route it had never seen and returned
``pass``, defaulting to ``http://localhost``. What the plugin guarantees now:

* no routes ⇒ ``unknown`` (nothing is invented and nothing is written);
* Pillow or the browser unavailable, a route that does not load ⇒ ``unknown``;
* a missing baseline ⇒ ``unknown`` ("nothing compared"), or with ``update_baselines`` the
  baseline is written and the result is ``skip`` with ``baseline_taken`` — never ``pass``;
* any mismatch or size change ⇒ ``fail``; every snapshot matched ⇒ ``pass``;
* dry run ⇒ ``skip``; no browser, no files.

Baselines live under ``visual_baselines/<browser>-<platform>/`` at the repository root,
whatever the working directory.
"""

from __future__ import annotations

import sys

from checks._browser import BrowserUnavailable
from checks._outcome import CheckOutcome
from checks._sink import JsonlSink
from checks.visual import runner
from checks.visual.baselines import BaselineStore, check_name
from checks.visual.compare import PillowUnavailable
from checks.visual.snapshot import KIND
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins._check_adapter import plugin_result


def _named_routes(raw: object) -> dict[str, str]:
    """``{name: url}`` from a mapping, or names derived from the path of each URL."""
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list | tuple):
        items = []
        for url in raw:
            if isinstance(url, str):
                tail = url.split("://", 1)[-1].split("/", 1)
                path = tail[1] if len(tail) > 1 else ""
                items.append(((path.strip("/").replace("/", "-") or "root")[:100], url))
    else:
        return {}
    out: dict[str, str] = {}
    for name, url in items:
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            try:
                out[check_name(str(name))] = url
            except ValueError:
                continue
    return out


class VisualRegressionPlugin(BasePlugin):
    name = "visual-regression"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["ui_change"]

    def run(self, context: dict) -> PluginResult:
        routes = _named_routes(context.get("routes"))
        if not routes:
            return plugin_result(
                CheckOutcome.unknown(KIND, "no routes were supplied: nothing compared")
            )
        if context.get("dry_run"):
            return plugin_result(
                CheckOutcome.skipped(KIND, "dry run: no browser started"), routes=routes
            )
        store = BaselineStore(
            root=context.get("baseline_root"), browser=context.get("browser", "chromium")
        )
        sink = context.get("metric_sink")
        if sink is None and context.get("record_metrics"):
            sink = JsonlSink()
        try:
            outcomes = runner.snapshot_routes(
                routes,
                store,
                update=bool(context.get("update_baselines")),
                pixel_threshold=float(context.get("pixel_threshold", 0.1)),
                max_diff_ratio=float(context.get("max_diff_ratio", 0.001)),
                sink=sink,
            )
        except (BrowserUnavailable, PillowUnavailable) as exc:
            return plugin_result(CheckOutcome.unknown(KIND, str(exc)), routes=routes)
        return plugin_result(runner.combine(outcomes), routes=routes)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--route", action="append", default=[], help="repeatable URL")
    parser.add_argument("--update-baselines", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"routes": args.route, "update_baselines": args.update_baselines}
    plugin = VisualRegressionPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
