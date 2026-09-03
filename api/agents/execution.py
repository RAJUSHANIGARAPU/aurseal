"""ExecutionAgent: run generated pytest files and parse results.

A run that did not happen must not be scored as a run that passed.

The agent used to read two things and neither of them was the run: it parsed
whatever sat at ``reports/pytest_report.json``, gated only on
``Path.exists()``, and it never looked at ``proc.returncode``. Both halves of
that fail in the same direction.

* **The stale report.** Give pytest an unknown flag and it exits 4 having
  executed nothing, so the json plugin never writes. The report still on disk
  is the *previous* run's. Measured: a report saying ``passed: 7``, a run that
  ran zero tests, and an ``ExecutionResult(passed=7, failed=0, errors=0)`` —
  which the orchestrator turned into ``success=True, final_pass_count=7``. A
  missing plugin, a collection crash or a killed process all land here too.
* **No report at all.** The stdout fallback finds no summary line and returns
  ``passed=0, failed=0, errors=0``, and "zero failures" is what a green suite
  looks like from the outside. Exit 5 — *no tests collected* — arrived the same
  way, and so did an empty ``test_files`` list.

So the exit code is now read first and is authoritative about whether there is
a verdict to read at all, the report is deleted before the run rather than
trusted to be current, and anything that could not produce a verdict says so in
``error`` instead of returning zeros that read as clean. ``ran`` is the single
question the caller should ask before believing any of the counts.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_REPORT_FILE = Path("reports/pytest_report.json")

# pytest's documented exit codes. 0 and 1 are the only two that mean "the suite
# ran and here is what happened"; the rest mean the run never reached a verdict.
_EXIT_ALL_PASSED = 0
_EXIT_TESTS_FAILED = 1
_NO_VERDICT_EXITS: dict[int, str] = {
    2: "pytest was interrupted before finishing (exit 2)",
    3: "pytest hit an internal error (exit 3)",
    4: "pytest usage error — an unknown flag or a plugin that failed to load (exit 4)",
    5: "pytest collected no tests (exit 5)",
}

# A hung request used to hang the orchestrator with it: subprocess.run had no
# timeout, and the loop runs this up to max_retries+1 times.
_DEFAULT_TIMEOUT_S = 900.0

# Whole tracebacks land in here and it is carried in memory through analysis and
# healing. Keep the head (the collection errors) and the tail (the summary and
# the FAILED lines both parsers look for) and drop the middle.
_MAX_RAW_OUTPUT = 200_000
_RAW_HEAD = 20_000


@dataclass
class ExecutionResult:
    """Result of one pytest run.

    Args:
        exit_code: pytest's exit code, or None when no process finished.
        error: why this run produced no verdict. Empty when it did — check
            ``ran`` rather than reading the counts of a run that never ran.
    """

    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration: float = 0.0
    raw_output: str = ""
    failure_details: list[dict] = field(default_factory=list)
    exit_code: int | None = None
    error: str = ""

    @property
    def ran(self) -> bool:
        """True when this run produced a verdict of its own.

        False means the counts are meaningless — not that they are zero.
        """
        return not self.error


class ExecutionAgent:
    """Runs pytest on generated test files and parses the JSON report."""

    def __init__(
        self,
        report_file: Path | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._report_file = report_file or _REPORT_FILE
        self._timeout = timeout
        self._json_reporter = self._ensure_json_reporter()

    @property
    def report_file(self) -> Path:
        return self._report_file

    def run(self, test_files: list[Path], extra_args: list[str] | None = None) -> ExecutionResult:
        """Execute the given test files under pytest.

        Args:
            test_files: Paths to pytest-compatible Python files.
            extra_args: Additional pytest arguments.

        Returns:
            ExecutionResult. Check ``ran`` before reading the counts.
        """
        if not test_files:
            logger.warning("ExecutionAgent.run called with empty file list")
            return ExecutionResult(error="no test files were given, so nothing was executed")

        self._report_file.parent.mkdir(parents=True, exist_ok=True)
        report_is_ours = self._clear_previous_report()
        str_files = [str(f) for f in test_files]

        cmd = [sys.executable, "-m", "pytest", *str_files]
        if self._json_reporter:
            cmd += ["--json-report", f"--json-report-file={self._report_file}"]
        cmd += ["-v", "--tb=short", "-p", "no:warnings"] + (extra_args or [])

        logger.info("Running: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("pytest timed out after %.0fs", self._timeout)
            return ExecutionResult(
                raw_output=self._cap(self._decode(exc.stdout) + self._decode(exc.stderr)),
                error=f"pytest timed out after {self._timeout:.0f}s and was killed",
            )

        raw_output = self._cap(proc.stdout + proc.stderr)
        logger.debug("pytest stdout:\n%s", proc.stdout)

        return self._parse_result(raw_output, proc.returncode, report_is_ours)

    def _clear_previous_report(self) -> bool:
        """Delete the last run's report. Returns False if it is still there.

        Deleting is the guard: `exists()` cannot tell this run's report from the
        one before it, and that is how a crashed run inherited ``passed: 7``.
        """
        try:
            self._report_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove previous report %s: %s", self._report_file, exc)
            return not self._report_file.exists()
        return True

    def _parse_result(
        self, raw_output: str, returncode: int, report_is_ours: bool
    ) -> ExecutionResult:
        no_verdict = _NO_VERDICT_EXITS.get(returncode)
        if no_verdict is None and returncode not in (_EXIT_ALL_PASSED, _EXIT_TESTS_FAILED):
            # Signals and anything else pytest does not define: 137 (OOM-killed)
            # is the common one, and it leaves a partial report behind.
            no_verdict = f"pytest exited with an unexpected code {returncode}"
        if no_verdict:
            logger.error("%s — no result to report", no_verdict)
            return ExecutionResult(raw_output=raw_output, exit_code=returncode, error=no_verdict)

        result = self._read_counts(raw_output, returncode, report_is_ours)

        # pytest says something failed and the counts say nothing did. One of
        # the two is wrong and there is no way to tell which, so report neither.
        if returncode == _EXIT_TESTS_FAILED and result.ran and result.failed + result.errors == 0:
            return ExecutionResult(
                raw_output=raw_output,
                exit_code=returncode,
                error="pytest exited 1 but the report shows no failure — counts not trustworthy",
            )
        return result

    def _read_counts(
        self, raw_output: str, returncode: int, report_is_ours: bool
    ) -> ExecutionResult:
        if report_is_ours and self._report_file.exists():
            try:
                return self._parse_json_report(raw_output, returncode)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse JSON report: %s — falling back to stdout", exc)
        elif self._report_file.exists():
            logger.warning(
                "Report %s could not be cleared before the run — refusing to read it",
                self._report_file,
            )

        return self._parse_stdout(raw_output, returncode)

    def _parse_json_report(self, raw_output: str, returncode: int) -> ExecutionResult:
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
            exit_code=returncode,
        )

    @staticmethod
    def _parse_stdout(raw_output: str, returncode: int) -> ExecutionResult:
        passed = failed = errors = 0
        duration = 0.0
        found_summary = False

        for line in raw_output.splitlines():
            if "passed" in line and ("failed" in line or "error" in line or "==" in line):
                parts = line.strip().split()
                for i, part in enumerate(parts):
                    if part == "passed":
                        try:
                            passed = int(parts[i - 1])
                            found_summary = True
                        except (ValueError, IndexError):
                            pass
                    elif part == "failed":
                        try:
                            failed = int(parts[i - 1])
                            found_summary = True
                        except (ValueError, IndexError):
                            pass
                    elif part == "error" or part == "errors":
                        try:
                            errors = int(parts[i - 1])
                            found_summary = True
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
                            found_summary = True
                        except (ValueError, IndexError):
                            pass

        if not found_summary:
            # No report and no summary line. The old code returned all-zeros
            # here, which every caller read as "nothing failed".
            return ExecutionResult(
                raw_output=raw_output,
                exit_code=returncode,
                error="pytest wrote no JSON report and its output carried no summary line",
            )

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
            exit_code=returncode,
        )

    @staticmethod
    def _cap(raw_output: str) -> str:
        if len(raw_output) <= _MAX_RAW_OUTPUT:
            return raw_output
        dropped = len(raw_output) - _MAX_RAW_OUTPUT
        tail = raw_output[-(_MAX_RAW_OUTPUT - _RAW_HEAD) :]
        return f"{raw_output[:_RAW_HEAD]}\n... [{dropped} characters dropped] ...\n{tail}"

    @staticmethod
    def _decode(stream: str | bytes | None) -> str:
        if stream is None:
            return ""
        if isinstance(stream, bytes):
            return stream.decode("utf-8", errors="replace")
        return stream

    @staticmethod
    def _ensure_json_reporter() -> bool:
        """Returns whether --json-report can be passed at all.

        Passing it without the plugin is itself a pytest usage error (exit 4),
        which is the crash that made the stale report readable in the first
        place. A failed install used to be swallowed by ``check=False``.
        """
        try:
            import pytest_jsonreport  # noqa: F401

            return True
        except ImportError:
            logger.info("Installing pytest-json-report...")

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "pytest-json-report", "-q"],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("pytest-json-report install failed (%s) — parsing stdout instead", exc)
            return False

        if proc.returncode != 0:
            logger.warning(
                "pytest-json-report install exited %d — parsing stdout instead: %s",
                proc.returncode,
                proc.stderr.strip()[:200],
            )
            return False

        try:
            import pytest_jsonreport  # noqa: F401

            return True
        except ImportError:
            logger.warning("pytest-json-report still not importable — parsing stdout instead")
            return False
