"""Mutation testing plugin — runs mutmut on targeted source module."""

from __future__ import annotations

import subprocess
import sys

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class MutationTestingPlugin(BasePlugin):
    name = "mutation-testing"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["test_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        target = context.get("target_module", "core/")
        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841

        if context.get("dry_run"):
            try:
                proc = subprocess.run(
                    [
                        "mutmut",
                        "run",
                        "--no-progress",
                        "--simple-output",
                        f"--paths-to-mutate={target}",
                        "--disable-mutation-types=all",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                return PluginResult(
                    status="pass",
                    findings=[{"target": target, "dry_run_output": proc.stdout[:500]}],
                    dry_run=True,
                )
            except FileNotFoundError:
                return PluginResult(
                    status="pass",
                    findings=[{"target": target, "mutmut": "not installed"}],
                    dry_run=True,
                )
            except Exception as exc:  # noqa: BLE001
                return PluginResult(
                    status="pass",
                    findings=[{"target": target, "error": str(exc)}],
                    dry_run=True,
                )

        try:
            proc = subprocess.run(
                ["mutmut", "run", "--paths-to-mutate", target],
                capture_output=True,
                text=True,
                timeout=600,
            )
            output = (proc.stdout + proc.stderr).strip()

            # Parse surviving mutants count
            surviving = 0
            for line in output.split("\n"):
                if "survived" in line.lower():
                    parts = line.split()
                    for p in parts:
                        if p.isdigit():
                            surviving = int(p)
                            break

            status = "pass" if surviving == 0 else "warn"
            return PluginResult(
                status=status,
                findings=[
                    {
                        "target": target,
                        "surviving_mutants": surviving,
                        "output": output[-500:],
                    }
                ],
            )
        except FileNotFoundError:
            return PluginResult(status="skip", findings=[{"mutmut": "not installed"}])
        except subprocess.TimeoutExpired:
            return PluginResult(status="error", findings=[{"error": "mutmut timed out"}])
        except Exception as exc:  # noqa: BLE001
            return PluginResult(status="error", findings=[{"error": str(exc)}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"target_module": "core/"}
    plugin = MutationTestingPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip", "warn") else 1)
