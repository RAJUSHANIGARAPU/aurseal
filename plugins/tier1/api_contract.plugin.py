"""API contract plugin — wraps contract_testing/ to validate API contracts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class ApiContractPlugin(BasePlugin):
    name = "api-contract"
    priority = PluginPriority.HIGH
    trigger_conditions = ["route_change", "api_change"]

    def run(self, context: dict) -> PluginResult:
        pact_dir = Path(context.get("pact_dir", "contract_testing"))

        if context.get("dry_run"):
            pact_files = list(pact_dir.rglob("*.json"))
            return PluginResult(
                status="pass",
                findings=[{"pact_files": [str(f) for f in pact_files], "count": len(pact_files)}],
                dry_run=True,
            )

        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841
        test_path = context.get("contract_test_path", "tests/contract_testing/")

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", test_path, "--tb=short", "-q"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            passed = proc.returncode == 0
            output_lines = (proc.stdout + proc.stderr).strip().split("\n")
            return PluginResult(
                status="pass" if passed else "fail",
                findings=[
                    {
                        "exit_code": proc.returncode,
                        "output": output_lines[-10:],
                    }
                ],
            )
        except subprocess.TimeoutExpired:
            return PluginResult(status="error", findings=[{"error": "contract tests timed out"}])
        except Exception as exc:  # noqa: BLE001
            return PluginResult(status="error", findings=[{"error": str(exc)}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = ApiContractPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
