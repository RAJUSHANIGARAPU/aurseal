"""Load performance plugin — a thin adapter over ``checks.load`` (k6 with thresholds).

The engine does the work; this file builds its inputs from the context and maps the
outcome. What the plugin guarantees:

* no routes, or only routes k6 cannot fetch ⇒ ``unknown`` (no target is ever invented);
* no profile, or a profile without thresholds ⇒ ``unknown``: k6 only fails on a crossed
  threshold, so a run with none could only ever pass;
* k6 absent, cut short, exiting with a non-threshold code, or leaving no summary ⇒
  ``unknown``;
* a crossed threshold ⇒ ``fail``, naming the threshold from the parsed summary;
* every threshold held on a run that made requests ⇒ ``pass``;
* dry run ⇒ ``skip`` with the rendered script path; k6 is not started.

The executed script is rendered deterministically from the routes and the profile, with
every value JSON-encoded; no model writes code that a subprocess then runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from checks._outcome import CheckOutcome
from checks._sink import REPO_ROOT, JsonlSink
from checks.load import k6
from checks.load.profile import LoadProfile, ProfileError
from checks.load.runner import DEFAULT_TIMEOUT_S, K6Runner, run_load, usable_routes
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins._check_adapter import plugin_result

_OUT_DIR = REPO_ROOT / "reports" / "load"


def _profile(raw: object) -> LoadProfile:
    if isinstance(raw, LoadProfile):
        return raw
    if isinstance(raw, dict):
        return LoadProfile.from_dict(raw)
    if isinstance(raw, str | Path):
        return LoadProfile.from_json(raw)
    raise ProfileError("no load profile was supplied: there are no thresholds to judge")


class LoadPerformancePlugin(BasePlugin):
    name = "load-performance"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["pre_deploy", "manual"]

    def run(self, context: dict) -> PluginResult:
        routes, rejected = usable_routes(context.get("routes"))
        if not routes:
            return plugin_result(
                CheckOutcome.unknown(
                    k6.KIND,
                    "no usable routes were supplied: nothing was load tested",
                    findings=[{"rejected_routes": rejected}],
                )
            )
        try:
            profile = _profile(context.get("profile"))
        except ProfileError as exc:
            return plugin_result(CheckOutcome.unknown(k6.KIND, f"profile refused: {exc}"))

        out_dir = Path(context.get("out_dir") or _OUT_DIR)
        if context.get("dry_run"):
            out_dir.mkdir(parents=True, exist_ok=True)
            script = out_dir / "load_test.js"
            script.write_text(k6.render_script(routes, profile), encoding="utf-8")
            return plugin_result(
                CheckOutcome.skipped(
                    k6.KIND, "dry run: script rendered, k6 not started", evidence=[script]
                ),
                script_path=str(script),
            )

        runner = K6Runner(context.get("k6_bin"), int(context.get("timeout_s", DEFAULT_TIMEOUT_S)))
        sink = context.get("metric_sink")
        if sink is None and context.get("record_metrics"):
            sink = JsonlSink()
        outcome = run_load(routes, profile, out_dir, runner=runner, sink=sink)
        extra = {"routes": routes}
        if rejected:
            extra["rejected_routes"] = rejected
        return plugin_result(outcome, **extra)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--route", action="append", default=[], help="repeatable target URL")
    parser.add_argument("--profile", default=None, help="load profile JSON (thresholds)")
    args = parser.parse_args()
    ctx: dict = {"routes": args.route, "profile": args.profile}
    plugin = LoadPerformancePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
