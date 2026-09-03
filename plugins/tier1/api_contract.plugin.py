"""
API contract plugin — wraps contract_testing/ to validate API contracts.

The verdict came from ``returncode == 0``, so every non-zero exit was published
as a contract failure. Two of those are not:

* ``contract_test_path`` defaulted to the relative ``"tests/contract_testing/"``,
  so launching the orchestrator from anywhere but the repository root gave
  pytest a path that does not exist. Pytest exits 4 — "I rejected your command
  line" — and this plugin reported a broken API contract. The path is now
  anchored to the repository root and its absence is ``unknown``: nothing was
  checked, so nothing is known.
* Exit 5 is "no tests were collected". The suite ran and verified nothing, which
  is also ``unknown``.

A genuine contract break — pytest exit 1 — is still ``fail``, and that is the
only thing that now reports one. See ``_pytest_exit.py``.
"""

from __future__ import annotations

import subprocess
import sys

from plugins._base_plugin import (
    BasePlugin,
    PluginPriority,
    PluginResult,
    PluginStatus,
    is_passing,
)
from plugins.tier1._paths import REPO_ROOT, resolve_dir
from plugins.tier1._pytest_exit import status_for_exit_code

_TIMEOUT_S = 120


class ApiContractPlugin(BasePlugin):
    name = "api-contract"
    priority = PluginPriority.HIGH
    trigger_conditions = ["route_change", "api_change"]

    def run(self, context: dict) -> PluginResult:
        pact_dir = resolve_dir(context, "pact_dir", "contract_testing")

        if context.get("dry_run"):
            pact_files = sorted(pact_dir.rglob("*.json")) if pact_dir.is_dir() else []
            # A dry run runs no contract test. It used to report `pass` and, in
            # the HIGH tier, carry 35% of the health score for having listed
            # some files.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "reason": "dry run — no contract test executed",
                        "pact_files": [str(f) for f in pact_files],
                        "count": len(pact_files),
                    }
                ],
                dry_run=True,
            )

        test_path = resolve_dir(context, "contract_test_path", "tests/contract_testing")
        if not test_path.is_dir():
            return _unknown(
                f"{test_path} does not exist — no contract test ran, so no contract was checked"
            )
        test_files = sorted(test_path.rglob("test_*.py"))
        if not test_files:
            return _unknown(f"{test_path} exists but holds no test_*.py — nothing was executed")

        timeout = int(context.get("timeout_s", _TIMEOUT_S))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "--tb=short", "-q"],
                capture_output=True,
                text=True,
                timeout=timeout,
                # Anchored: a relative rootdir is how the same command produced
                # different collections depending on where it was launched.
                cwd=str(REPO_ROOT),
            )
        except subprocess.TimeoutExpired:
            # The plugin did not break — the suite did not finish. That is an
            # absent verdict, not a contract break and not a crash.
            return _unknown(f"the suite did not finish within {timeout}s; no verdict was reached")
        except Exception as exc:  # noqa: BLE001
            return PluginResult(
                status=PluginStatus.ERROR.value,
                findings=[{"error": f"{type(exc).__name__}: {exc}"}],
            )

        status, reason = status_for_exit_code(proc.returncode)
        output_lines = (proc.stdout + proc.stderr).strip().split("\n")
        return PluginResult(
            status=status,
            findings=[
                {
                    "exit_code": proc.returncode,
                    "reason": reason,
                    "contract_test_path": str(test_path),
                    "test_files": len(test_files),
                    "output": output_lines[-10:],
                }
            ],
        )


def _unknown(reason: str) -> PluginResult:
    return PluginResult(status=PluginStatus.UNKNOWN.value, findings=[{"reason": reason}])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {}
    plugin = ApiContractPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if is_passing(result.status) or result.status == PluginStatus.SKIP.value else 1)
