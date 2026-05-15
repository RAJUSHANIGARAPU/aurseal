#!/usr/bin/env python3
"""
Autonomous test runner: run → Claude decides → fix/analyze/generate → rerun.

Default mode: Claude uses tool_use to decide what action to take after each failure.
Legacy mode:  hard-coded sequential analyze → fix → rerun (use --legacy).

Usage:
    python scripts/auto_runner.py
    python scripts/auto_runner.py -k "test_wiki_search"
    python scripts/auto_runner.py --max-iterations 3
    python scripts/auto_runner.py --no-fix          # analyze only, no code changes
    python scripts/auto_runner.py --legacy          # old sequential behavior

Requirements: ANTHROPIC_API_KEY env var.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path


def run_tests(pytest_args: list[str]) -> tuple[int, str]:
    """Run pytest and return (exit_code, combined output)."""
    cmd = (
        [sys.executable, "-m", "pytest"]
        + pytest_args
        + ["--tb=short", "--junit-xml=reports/junit.xml", "-v"]
    )
    print(f"\n{'=' * 60}")
    print(f"Running: {' '.join(cmd)}")
    print("=" * 60)

    result = subprocess.run(cmd, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    print(combined[-3000:] if len(combined) > 3000 else combined)
    return result.returncode, combined


# ---------------------------------------------------------------------------
# Agent mode (default)
# ---------------------------------------------------------------------------


def _agent_loop(
    pytest_args: list[str],
    max_iterations: int,
    no_fix: bool,
) -> int:
    from core.agents.planner_agent import PlannerAgent

    planner = PlannerAgent()

    for iteration in range(1, max_iterations + 1):
        print(f"\n{'#' * 60}")
        print(f"# ITERATION {iteration}/{max_iterations}  [agent mode]")
        print("#" * 60)

        exit_code, output = run_tests(pytest_args)

        if exit_code == 0:
            print("\nALL TESTS PASSED")
            return 0

        print(f"\nTests failed (exit {exit_code}). Asking planner agent what to do...")
        result = planner.plan(output, exit_code, iteration, analyze_only=no_fix)

        print(f"\n[PLANNER] status={result.status} | {result.reason}")
        for action in result.actions:
            print(f"  • {action}")

        if result.status == "blocked":
            print("\nPlanner blocked — stopping loop.")
            break

        if result.status == "passed" and iteration < max_iterations:
            print("\nPlanner applied fixes. Waiting 2s before rerun...")
            time.sleep(2)
            continue

        # status == "failed" or last iteration
        break

    print(f"\n--- FINAL STATUS: FAILED after {max_iterations} iteration(s) ---")
    return 1


# ---------------------------------------------------------------------------
# Legacy mode (--legacy)
# ---------------------------------------------------------------------------


def _extract_failed_files(output: str) -> list[str]:
    files: set[str] = set()
    for match in re.finditer(r"FAILED\s+([\w/]+\.py)", output):
        files.add(match.group(1))
    for match in re.finditer(r'File "([\w/]+\.py)", line', output):
        files.add(match.group(1))
    return [f for f in files if Path(f).exists()]


def _legacy_loop(
    pytest_args: list[str],
    max_iterations: int,
    no_fix: bool,
) -> int:
    from core.ai.auto_fixer import AutoFixer
    from core.ai.log_analyzer import LogAnalyzer
    from core.ai.trace_analyzer import TraceAnalyzer

    analyzer = LogAnalyzer()
    fixer = AutoFixer()
    trace_analyzer = TraceAnalyzer()

    for iteration in range(1, max_iterations + 1):
        print(f"\n{'#' * 60}")
        print(f"# ITERATION {iteration}/{max_iterations}  [legacy mode]")
        print("#" * 60)

        exit_code, output = run_tests(pytest_args)

        if exit_code == 0:
            print("\nALL TESTS PASSED")
            return 0

        print(f"\nTests failed (exit code {exit_code}). Analyzing...")

        diagnosis = analyzer.analyze_failures()
        print(f"\n--- AI DIAGNOSIS ---\n{diagnosis}\n---")

        latest_trace = trace_analyzer.find_latest_trace()
        if latest_trace:
            trace_report = trace_analyzer.analyze(latest_trace)
            print(f"\n--- TRACE ANALYSIS ---\n{trace_report}\n---")

        if no_fix or iteration == max_iterations:
            break

        failed_files = _extract_failed_files(output)
        if not failed_files:
            print("Could not identify files to fix from output.")
            break

        fixed_any = False
        for file_path in failed_files:
            print(f"\nAttempting fix for: {file_path}")
            if fixer.fix_file(file_path, diagnosis, output[-2000:]):
                fixed_any = True

        if not fixed_any:
            print("No fixes were applied. Stopping loop.")
            break

        print("\nFixes applied. Waiting 2s before rerun...")
        time.sleep(2)

    print(f"\n--- FINAL STATUS: FAILED after {max_iterations} iteration(s) ---")
    return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous pytest run-analyze-fix loop")
    parser.add_argument("-k", "--keyword", help="pytest -k filter")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument(
        "--no-fix",
        action="store_true",
        help="Analyze only — do not apply any code changes",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use hard-coded sequential analyze→fix loop instead of Claude tool_use",
    )
    args = parser.parse_args()

    pytest_args: list[str] = []
    if args.keyword:
        pytest_args += ["-k", args.keyword]

    if args.legacy:
        return _legacy_loop(pytest_args, args.max_iterations, args.no_fix)
    return _agent_loop(pytest_args, args.max_iterations, args.no_fix)


if __name__ == "__main__":
    sys.exit(main())
