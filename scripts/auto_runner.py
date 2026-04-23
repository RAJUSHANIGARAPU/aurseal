#!/usr/bin/env python3
"""
Autonomous test runner: run → analyze → fix → rerun until all tests pass.

Usage:
    python scripts/auto_runner.py
    python scripts/auto_runner.py -k "test_wiki_search"
    python scripts/auto_runner.py --max-iterations 3

Requirements: ANTHROPIC_API_KEY env var for AI fix suggestions.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from core.ai.auto_fixer import AutoFixer
from core.ai.log_analyzer import LogAnalyzer
from core.ai.trace_analyzer import TraceAnalyzer


def run_tests(pytest_args: list[str]) -> tuple[int, str]:
    """Run pytest and return (exit_code, output)."""
    cmd = (
        [sys.executable, "-m", "pytest"]
        + pytest_args
        + [
            "--tb=short",
            "--junit-xml=reports/junit.xml",
            "-v",
        ]
    )
    print(f"\n{'=' * 60}")
    print(f"Running: {' '.join(cmd)}")
    print("=" * 60)

    result = subprocess.run(cmd, capture_output=True, text=True)
    combined = result.stdout + result.stderr
    print(combined[-3000:] if len(combined) > 3000 else combined)
    return result.returncode, combined


def extract_failed_files(output: str) -> list[str]:
    """Extract Python file paths from pytest failure output."""
    import re

    files = set()
    # FAILED ui/tests/test_wiki.py::TestWiki::test_search
    for match in re.finditer(r"FAILED\s+([\w/]+\.py)", output):
        files.add(match.group(1))
    # E   playwright._impl... File "ui/pages/wiki_page.py", line 42
    for match in re.finditer(r'File "([\w/]+\.py)", line', output):
        files.add(match.group(1))
    return [f for f in files if Path(f).exists()]


def main():
    parser = argparse.ArgumentParser(description="Autonomous pytest run-analyze-fix loop")
    parser.add_argument("-k", "--keyword", help="pytest -k filter")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--no-fix", action="store_true", help="Analyze only, do not apply fixes")
    args = parser.parse_args()

    pytest_args = []
    if args.keyword:
        pytest_args += ["-k", args.keyword]

    analyzer = LogAnalyzer()
    fixer = AutoFixer()
    trace_analyzer = TraceAnalyzer()

    for iteration in range(1, args.max_iterations + 1):
        print(f"\n{'#' * 60}")
        print(f"# ITERATION {iteration}/{args.max_iterations}")
        print("#" * 60)

        exit_code, output = run_tests(pytest_args)

        if exit_code == 0:
            print("\nALL TESTS PASSED")
            return 0

        print(f"\nTests failed (exit code {exit_code}). Analyzing...")

        # Analyze failures
        diagnosis = analyzer.analyze_failures()
        print(f"\n--- AI DIAGNOSIS ---\n{diagnosis}\n---")

        # Analyze latest trace
        latest_trace = trace_analyzer.find_latest_trace()
        if latest_trace:
            trace_report = trace_analyzer.analyze(latest_trace)
            print(f"\n--- TRACE ANALYSIS ---\n{trace_report}\n---")

        if args.no_fix or iteration == args.max_iterations:
            break

        # Attempt fixes
        failed_files = extract_failed_files(output)
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

    print(f"\n--- FINAL STATUS: FAILED after {args.max_iterations} iteration(s) ---")
    print("Review the diagnosis above and the latest trace at: https://trace.playwright.dev")
    return 1


if __name__ == "__main__":
    sys.exit(main())
