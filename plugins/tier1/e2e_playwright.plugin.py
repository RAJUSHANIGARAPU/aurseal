"""E2E Playwright plugin — wraps ui/tests/ for end-to-end test execution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class E2EPlaywrightPlugin(BasePlugin):
    name = "e2e-playwright"
    priority = PluginPriority.HIGH
    trigger_conditions = ["ui_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        ui_tests_dir = Path(context.get("ui_tests_dir", "ui/tests"))

        if context.get("dry_run"):
            test_files = list(ui_tests_dir.rglob("test_*.py")) if ui_tests_dir.exists() else []
            return PluginResult(
                status="pass",
                findings=[{"test_files": [str(f) for f in test_files], "count": len(test_files)}],
                dry_run=True,
            )

        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841

        if not ui_tests_dir.exists():
            return PluginResult(
                status="skip",
                findings=[{"reason": f"{ui_tests_dir} does not exist"}],
            )

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(ui_tests_dir), "--tb=short", "-q"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            passed = proc.returncode == 0
            output_lines = (proc.stdout + proc.stderr).strip().split("\n")
            return PluginResult(
                status="pass" if passed else "fail",
                findings=[
                    {
                        "exit_code": proc.returncode,
                        "output": output_lines[-20:],
                    }
                ],
            )
        except subprocess.TimeoutExpired:
            return PluginResult(status="error", findings=[{"error": "e2e tests timed out"}])
        except Exception as exc:  # noqa: BLE001
            return PluginResult(status="error", findings=[{"error": str(exc)}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = E2EPlaywrightPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
