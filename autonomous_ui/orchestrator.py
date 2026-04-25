"""Autonomous UI test orchestrator.

Loop: run pytest → collect failure bundles → analyze → heal → rerun --lf
Stops when all tests pass, no healing was applied, or max_iterations reached.

Usage:
    python -m autonomous_ui.orchestrator
    python -m autonomous_ui.orchestrator --path ui/tests/test_search.py --max-iterations 3
    python -m autonomous_ui.orchestrator --analyze-only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from api.llm.base import BaseLLMClient
from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.analyzer import FailureAnalyzer
from autonomous_ui.healer import UIHealer
from autonomous_ui.models import FailureBundle, HealingResult

_BUNDLES_DIR = Path("reports/failures")
_SESSION_LOG = Path("reports/ui_healing_sessions.jsonl")
_HEALING_OVERRIDES = Path("reports/healing_overrides.json")


class UIOrchestrator:
    """Drives the autonomous run → analyze → heal → rerun cycle."""

    def __init__(self, llm: BaseLLMClient | None = None) -> None:
        effective_llm = llm or ClaudeLLMClient()
        self._analyzer = FailureAnalyzer(llm=effective_llm)
        self._healer = UIHealer(llm=effective_llm)

    def run(
        self,
        pytest_args: list[str] | None = None,
        max_iterations: int = 3,
        analyze_only: bool = False,
    ) -> int:
        args = list(pytest_args or [])
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            print(f"\n{'=' * 60}")
            print(f"UI ORCHESTRATOR — iteration {iteration}/{max_iterations}")
            print("=" * 60)

            run_start = datetime.now(tz=timezone.utc)
            exit_code = self._run_pytest(args)

            if exit_code == 0:
                print("\nAll tests passed.")
                return 0

            bundles = self._collect_bundles_since(run_start)
            if not bundles:
                print("\nTests failed but no failure bundles found. Check conftest.py integration.")
                break

            healed_any = False
            for bundle in bundles:
                analysis = self._analyzer.analyze(bundle, use_llm=True)
                print(
                    f"\n[ANALYSIS] {bundle.test} | type={analysis.failure_type.value} "
                    f"| confidence={analysis.confidence}"
                )
                print(f"  root cause: {analysis.root_cause}")
                if analysis.llm_suggestion:
                    print(f"  llm hint  : {analysis.llm_suggestion[:200]}")

                if analyze_only:
                    continue

                result = self._healer.heal(analysis, dom_snapshot=bundle.dom_snapshot)
                self._log_session(analysis, result)
                print(
                    f"  healing   : strategy={result.strategy} applied={result.applied}"
                    f" | {result.details}"
                )
                if result.applied:
                    healed_any = True

            if analyze_only or not healed_any:
                print("\nNo healing applied — stopping loop. Review analysis above.")
                break

            # Next iteration runs only the tests that failed last time
            if "--lf" not in args:
                args = ["--lf"] + args

            # Respect retry overrides written by the healer
            retry_extra = self._retry_args()
            for flag in retry_extra:
                if flag not in args:
                    args.append(flag)

            print(f"\nHealing applied. Waiting 1s then rerunning: {' '.join(args)}")
            time.sleep(1)

        return 1

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_pytest(args: list[str]) -> int:
        cmd = [sys.executable, "-m", "pytest", "--tb=short", "-v"] + args
        print(f"Running: {' '.join(cmd)}\n")
        result = subprocess.run(cmd)
        return result.returncode

    @staticmethod
    def _collect_bundles_since(since: datetime) -> list[FailureBundle]:
        bundles: list[FailureBundle] = []
        if not _BUNDLES_DIR.exists():
            return bundles
        for path in sorted(_BUNDLES_DIR.glob("*.json")):
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < since:
                continue
            try:
                data = json.loads(path.read_text())
                bundles.append(FailureBundle.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                pass
        return bundles

    @staticmethod
    def _retry_args() -> list[str]:
        if not _HEALING_OVERRIDES.exists():
            return []
        try:
            overrides = json.loads(_HEALING_OVERRIDES.read_text())
        except json.JSONDecodeError:
            return []
        if overrides.get("retry_tests"):
            return ["--reruns", "2", "--reruns-delay", "1"]
        return []

    @staticmethod
    def _log_session(analysis, result: HealingResult) -> None:
        _SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "test": analysis.test_name,
            "failure_type": analysis.failure_type.value,
            "root_cause": analysis.root_cause,
            "confidence": analysis.confidence,
            "strategy": result.strategy,
            "applied": result.applied,
            "details": result.details,
            "patched_files": [str(p) for p in result.patched_files],
        }
        with open(_SESSION_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous UI test orchestrator")
    parser.add_argument(
        "--path",
        help="pytest path or pattern to run (default: ui/tests/)",
        default="ui/tests/",
    )
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("-k", "--keyword", help="pytest -k filter")
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="run and analyze failures without applying any healing",
    )
    args = parser.parse_args()

    pytest_args = [args.path]
    if args.keyword:
        pytest_args += ["-k", args.keyword]

    orchestrator = UIOrchestrator()
    return orchestrator.run(
        pytest_args=pytest_args,
        max_iterations=args.max_iterations,
        analyze_only=args.analyze_only,
    )


if __name__ == "__main__":
    sys.exit(main())
