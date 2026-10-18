"""Fault injection plugin — runs the ``chaos`` tests and reports what they established.

It replaces the chaos-resilience plugin, which asked a model to describe failure scenarios
and returned ``pass`` when the model replied: it injected nothing and observed nothing.
This one runs real fault-injection tests (``checks.faults``) and maps pytest's result:

* exit 0 with at least one test passed ⇒ ``pass``;
* exit 0 with every test skipped (a fault never fired: ``held_empty``) ⇒ ``unknown``;
* exit 1 ⇒ ``fail``; 2/3/4 ⇒ ``error``; 5 (nothing collected) ⇒ ``unknown``;
* dry run ⇒ ``skip``; nothing is started.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET  # noqa: N817 - parses the junit file this plugin wrote
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult, PluginStatus
from plugins.tier1._paths import REPO_ROOT
from plugins.tier1._pytest_exit import status_for_exit_code

DEFAULT_PATHS = ("tests/demo_examples/chaos",)
_TIMEOUT_S = 1800


def _tally(junit: Path) -> dict[str, int]:
    try:
        root = ET.parse(junit).getroot()  # noqa: S314 - our own junit output
    except (OSError, ET.ParseError):
        return {}
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in total:
            total[key] += int(suite.get(key, 0))
    total["passed"] = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    return total


class FaultInjectionPlugin(BasePlugin):
    name = "fault-injection"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["pre_deploy", "manual"]

    def run(self, context: dict) -> PluginResult:
        paths = [str(p) for p in (context.get("paths") or DEFAULT_PATHS)]
        marker = context.get("marker", "chaos")
        if context.get("dry_run"):
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[{"paths": paths, "marker": marker, "reason": "dry run"}],
                dry_run=True,
            )
        with tempfile.TemporaryDirectory() as tmp:
            junit = Path(tmp) / "chaos.xml"
            cmd = [sys.executable, "-m", "pytest", *paths, "-m", marker, "-p", "no:cacheprovider"]
            cmd += [f"--junitxml={junit}", *context.get("pytest_args", [])]
            try:
                proc = subprocess.run(
                    cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=_TIMEOUT_S
                )
            except subprocess.TimeoutExpired:
                return PluginResult(
                    status=PluginStatus.UNKNOWN.value,
                    findings=[{"reason": f"chaos tests did not finish within {_TIMEOUT_S}s"}],
                )
            tally = _tally(junit)
        status, reason = status_for_exit_code(proc.returncode)
        if status == PluginStatus.PASS.value and tally.get("passed", 0) == 0:
            status = PluginStatus.UNKNOWN.value
            reason = "every chaos test was skipped: no fault was exercised"
        return PluginResult(
            status=status,
            findings=[
                {
                    "reason": reason,
                    "exit_code": proc.returncode,
                    "tally": tally,
                    "output": (proc.stdout + proc.stderr)[-500:],
                }
            ],
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", action="append", default=None)
    args = parser.parse_args()
    ctx: dict = {"paths": args.path}
    plugin = FaultInjectionPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
