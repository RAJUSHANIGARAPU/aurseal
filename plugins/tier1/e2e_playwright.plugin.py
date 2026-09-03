"""
E2E Playwright plugin — wraps ui/tests/ for end-to-end test execution.

A missing test directory returned ``skip``, which was wrong twice over. It is
not "not applicable to this run" — it is "I could not run", which the status
vocabulary now spells ``unknown``. And on a ``ui_change`` trigger this plugin is
the entire HIGH tier, so a ``skip`` left that tier out of the health fraction
altogether: a wrong working directory bought 35 points having driven no browser.
Both halves of that are fixed here — the directory is anchored to the repository
root instead of the process's cwd, and its absence is reported as no verdict.

The other silent hole was the exit code. Everything except 0 was ``fail``,
including pytest's exit 5 — "no tests were collected" — which is a suite that
ran and proved nothing. See ``_pytest_exit.py``.
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

_TIMEOUT_S = 300


class E2EPlaywrightPlugin(BasePlugin):
    name = "e2e-playwright"
    priority = PluginPriority.HIGH
    trigger_conditions = ["ui_change", "manual"]

    def run(self, context: dict) -> PluginResult:
        ui_tests_dir = resolve_dir(context, "ui_tests_dir", "ui/tests")
        test_files = sorted(ui_tests_dir.rglob("test_*.py")) if ui_tests_dir.is_dir() else []

        if context.get("dry_run"):
            # A dry run drives no browser, so it is evidence of nothing. It used
            # to report `pass` and, in the HIGH tier, carry 35% of the score.
            return PluginResult(
                status=PluginStatus.SKIP.value,
                findings=[
                    {
                        "reason": "dry run — no browser test executed",
                        "test_files": [str(f) for f in test_files],
                        "count": len(test_files),
                    }
                ],
                dry_run=True,
            )

        if not ui_tests_dir.is_dir():
            return _unknown(
                f"{ui_tests_dir} does not exist — no browser test ran, so this run "
                f"says nothing about the UI"
            )
        if not test_files:
            return _unknown(f"{ui_tests_dir} exists but holds no test_*.py — nothing was executed")

        timeout = int(context.get("timeout_s", _TIMEOUT_S))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", str(ui_tests_dir), "--tb=short", "-q"],
                capture_output=True,
                text=True,
                timeout=timeout,
                # Anchored: a relative rootdir is how the same command produced
                # different collections depending on where it was launched.
                cwd=str(REPO_ROOT),
            )
        except subprocess.TimeoutExpired:
            # The plugin did not break — the suite did not finish. That is an
            # absent verdict, not a product failure and not a crash.
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
                    "ui_tests_dir": str(ui_tests_dir),
                    "test_files": len(test_files),
                    "output": output_lines[-20:],
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
    plugin = E2EPlaywrightPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if is_passing(result.status) or result.status == PluginStatus.SKIP.value else 1)
