"""ExecutionAgent: run generated pytest files and parse results."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_REPORT_FILE = Path("reports/pytest_report.json")


@dataclass
class ExecutionResult:
    """Result of one pytest run."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration: float = 0.0
    raw_output: str = ""
    failure_details: list[dict] = field(default_factory=list)


class ExecutionAgent:
    """Runs pytest on generated test files and parses the JSON report."""

    def __init__(self, report_file: Path | None = None) -> None:
        self._report_file = report_file or _REPORT_FILE
        self._ensure_json_reporter()

    def run(self, test_files: list[Path], extra_args: list[str] | None = None) -> ExecutionResult:
        """Execute the given test files under pytest.

        Args:
            test_files: Paths to pytest-compatible Python files.
            extra_args: Additional pytest arguments.

        Returns:
            ExecutionResult with pass/fail counts and raw output.
        """
        if not test_files:
            logger.warning("ExecutionAgent.run called with empty file list")
            return ExecutionResult()

        self._report_file.parent.mkdir(parents=True, exist_ok=True)
        str_files = [str(f) for f in test_files]

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *str_files,
            "--json-report",
            f"--json-report-file={self._report_file}",
            "-v",
            "--tb=short",
            "-p",
            "no:warnings",
        ] + (extra_args or [])

        logger.info("Running: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        raw_output = proc.stdout + proc.stderr
        logger.debug("pytest stdout:\n%s", proc.stdout)

        return self._parse_result(raw_output)

    def _parse_result(self, raw_output: str) -> ExecutionResult:
        if self._report_file.exists():
            try:
                return self._parse_json_report(raw_output)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse JSON report: %s — falling back to stdout", exc)

        return self._parse_stdout(raw_output)

    def _parse_json_report(self, raw_output: str) -> ExecutionResult:
        with open(self._report_file, encoding="utf-8") as fh:
            report = json.load(fh)

        summary = report.get("summary", {})
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        errors = summary.get("error", 0)
        duration = report.get("duration", 0.0)

        failure_details: list[dict] = []
        for test in report.get("tests", []):
            if test.get("outcome") in ("failed", "error"):
                failure_details.append(
                    {
                        "test_name": test.get("nodeid", ""),
                        "outcome": test.get("outcome"),
                        "message": test.get("call", {}).get("longrepr", "")
                        if test.get("call")
                        else test.get("setup", {}).get("longrepr", ""),
                    }
                )

        return ExecutionResult(
            passed=passed,
            failed=failed,
            errors=errors,
            duration=duration,
            raw_output=raw_output,
            failure_details=failure_details,
        )

    @staticmethod
    def _parse_stdout(raw_output: str) -> ExecutionResult:
        passed = failed = errors = 0
        duration = 0.0

        for line in raw_output.splitlines():
            if "passed" in line and ("failed" in line or "error" in line or "==" in line):
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == "passed":
                        try:
                            passed = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass
                    elif part == "failed":
                        try:
                            failed = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass
                    elif part == "error" or part == "errors":
                        try:
                            errors = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass
                    elif part == "in" and i + 1 < len(parts):
                        try:
                            duration = float(parts[i + 1].replace("s", ""))
                        except ValueError:
                            pass
            elif "passed" in line and "==" in line:
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == "passed":
                        try:
                            passed = int(parts[i - 1])
                        except (ValueError, IndexError):
                            pass

        failure_details: list[dict] = []
        current_test: str | None = None
        current_lines: list[str] = []

        for line in raw_output.splitlines():
            if line.startswith("FAILED "):
                name = line.replace("FAILED ", "").split(" - ")[0].strip()
                if current_test and current_lines:
                    failure_details.append(
                        {"test_name": current_test, "message": "\n".join(current_lines)}
                    )
                current_test = name
                current_lines = [line]
            elif current_test:
                current_lines.append(line)

        if current_test and current_lines:
            failure_details.append({"test_name": current_test, "message": "\n".join(current_lines)})

        return ExecutionResult(
            passed=passed,
            failed=failed,
            errors=errors,
            duration=duration,
            raw_output=raw_output,
            failure_details=failure_details,
        )

    def _ensure_json_reporter(self) -> None:
        try:
            import pytest_jsonreport  # noqa: F401
        except ImportError:
            logger.info("Installing pytest-json-report...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "pytest-json-report", "-q"],
                check=False,
            )
