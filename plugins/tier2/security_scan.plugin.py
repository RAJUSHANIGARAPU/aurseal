"""Security scan plugin — a thin adapter over ``checks.security``.

The old plugin grepped source files with two regexes (false positives on any test token,
nothing on real ones), ran ``pip-audit`` and then ignored its result: a CVE could never
change the status. It also walked ``.venv``. What the plugin guarantees now:

* gitleaks (secrets in git history), bandit (first-party code, tests and ``.venv`` excluded)
  and pip-audit (the pinned lock file) are the tools; ``context['tools']`` narrows them;
* a blocking finding — anything not covered by an unexpired ``security/allowlist.yml``
  entry, a pip-audit vulnerability included — or an expired entry ⇒ ``fail``;
* any requested tool missing or without an answer ⇒ ``unknown``, never ``pass``;
* every requested tool ran and nothing blocks ⇒ ``pass``;
* dry run ⇒ ``skip``; nothing is started.
"""

from __future__ import annotations

import sys
from pathlib import Path

from checks._outcome import CheckOutcome
from checks._sink import REPO_ROOT, JsonlSink
from checks.security import scan as security_scan
from checks.security.allowlist import Allowlist
from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins._check_adapter import plugin_result


class SecurityScanPlugin(BasePlugin):
    name = "security-scan"
    priority = PluginPriority.HIGH
    trigger_conditions = ["deploy", "manual"]

    def run(self, context: dict) -> PluginResult:
        requested = list(context.get("tools") or security_scan.TOOLS)
        if context.get("dry_run"):
            return plugin_result(
                CheckOutcome.skipped(security_scan.KIND, "dry run: no tool started"),
                tools=requested,
            )
        root = Path(context.get("source_dir") or REPO_ROOT)
        if not root.is_absolute():
            root = (REPO_ROOT / root).resolve()
        try:
            allowlist = Allowlist.load(context.get("allowlist") or security_scan.DEFAULT_ALLOWLIST)
        except ValueError as exc:
            return plugin_result(
                CheckOutcome.error(security_scan.KIND, f"allow-list rejected: {exc}")
            )
        sink = context.get("metric_sink")
        if sink is None and context.get("record_metrics"):
            sink = JsonlSink()
        outcome = security_scan.scan(
            requested,
            root=root,
            lockfile=(
                Path(context["lockfile"])
                if context.get("lockfile")
                else security_scan.DEFAULT_LOCKFILES
            ),
            allowlist=allowlist,
            sink=sink,
        )
        return plugin_result(outcome, tools=requested)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tools", default=",".join(security_scan.TOOLS))
    args = parser.parse_args()
    ctx: dict = {"tools": [t for t in args.tools.split(",") if t]}
    plugin = SecurityScanPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
