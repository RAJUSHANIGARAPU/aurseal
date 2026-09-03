"""Shared stubs for the execution/orchestration tests.

Nothing here launches pytest or touches the network. ``FakePytest`` replaces
``subprocess.run`` so an exit code — and whether a JSON report was written at
all — can be chosen per test; that pairing is the whole point, because the
defect these tests pin is a crashed run (no report) being scored from the
report the *previous* run left on disk.

``FakeExecution``/``FakeGeneration``/``FakeIngestion``/``FakeHealing`` replace
the agents the Orchestrator builds for itself, so an orchestration run can be
driven to a chosen shape without generating or executing anything.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from api.agents.execution import ExecutionResult


def report_payload(
    passed: int = 0,
    failed: int = 0,
    errors: int = 0,
    duration: float = 0.1,
    tests: list[dict] | None = None,
) -> dict:
    """A pytest-json-report document with the summary counts a run would write."""
    return {
        "summary": {"passed": passed, "failed": failed, "error": errors},
        "duration": duration,
        "tests": tests or [],
    }


def failed_test_entry(nodeid: str, message: str = "AssertionError: boom") -> dict:
    return {"nodeid": nodeid, "outcome": "failed", "call": {"longrepr": message}}


class FakePytest:
    """Replaces ``subprocess.run``. Records argv/kwargs, optionally writes a report.

    Args:
        returncode: what the pytest process exits with.
        report: payload to write to the ``--json-report-file`` path. ``None``
            means the run wrote nothing — a crash before the plugin's hook, or
            a usage error that never reached collection.
        raises: raised instead of returning, for the timeout path.
    """

    def __init__(
        self,
        returncode: int = 0,
        report: dict | None = None,
        stdout: str = "",
        stderr: str = "",
        raises: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.report = report
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> FakePytest:  # noqa: ANN001
        monkeypatch.setattr(subprocess, "run", self._run)
        return self

    @property
    def pytest_calls(self) -> list[dict]:
        """Only the pytest invocations — pip installs are filtered out."""
        return [c for c in self.calls if "pytest" in c["argv"]]

    def _run(self, argv, **kwargs):  # noqa: ANN001
        self.calls.append({"argv": list(argv), "kwargs": kwargs})
        if self.raises is not None:
            raise self.raises
        if self.report is not None:
            for arg in argv:
                if str(arg).startswith("--json-report-file="):
                    path = Path(str(arg).split("=", 1)[1])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(self.report), encoding="utf-8")
        return FakeProcess(self.returncode, self.stdout, self.stderr)


@dataclass
class FakeProcess:
    """A finished ``subprocess.run`` result with a chosen exit code."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class FakeIngestion:
    """Parses nothing — hands back a fixed request list."""

    def __init__(self, requests: list | None = None) -> None:
        self._requests = requests if requests is not None else [object()]

    def parse_file(self, path) -> list:  # noqa: ANN001, ARG002
        return list(self._requests)


class FakeGeneration:
    """Writes nothing — hands back a fixed file list."""

    def __init__(self, files: list[Path]) -> None:
        self._files = files

    def __call__(self, *args, **kwargs) -> FakeGeneration:  # noqa: ANN002, ANN003, ARG002
        return self

    def generate(self, requests: list) -> list[Path]:  # noqa: ARG002
        return list(self._files)


class FakeExecution:
    """Returns canned ``ExecutionResult``s, in order; the last one repeats."""

    def __init__(self, *results: ExecutionResult) -> None:
        self.results = list(results) or [ExecutionResult(exit_code=0)]
        self.runs = 0
        self.report_file = Path("reports") / "does-not-exist.json"

    def __call__(self, *args, **kwargs) -> FakeExecution:  # noqa: ANN002, ANN003, ARG002
        return self

    def run(self, test_files: list[Path], extra_args: list | None = None) -> ExecutionResult:  # noqa: ARG002
        result = self.results[min(self.runs, len(self.results) - 1)]
        self.runs += 1
        return result


@dataclass
class HealCall:
    file: Path
    test_names: list[str]


@dataclass
class FakeHealResult:
    fixed: bool = True
    changes_made: list[str] = field(default_factory=lambda: ["stub"])


class FakeHealing:
    """Records which analyses were handed to which file, and heals nothing."""

    def __init__(self) -> None:
        self.calls: list[HealCall] = []

    def __call__(self, *args, **kwargs) -> FakeHealing:  # noqa: ANN002, ANN003, ARG002
        return self

    def heal(self, analyses: list, test_file: Path) -> FakeHealResult:
        self.calls.append(HealCall(file=test_file, test_names=[a.test_name for a in analyses]))
        return FakeHealResult(fixed=False)

    def names_for(self, filename: str) -> list[str]:
        """Every analysis name this file was ever healed with."""
        names: list[str] = []
        for call in self.calls:
            if call.file.name == filename:
                names.extend(call.test_names)
        return names


def install_orchestrator_fakes(
    monkeypatch,  # noqa: ANN001
    *,
    generated: list[Path],
    execution: FakeExecution | None,
    healing: FakeHealing | None = None,
) -> FakeHealing:
    """Swap out the agents the Orchestrator constructs, so nothing real runs.

    ``execution=None`` leaves the real ExecutionAgent in place — for the one
    test that has to exercise the seam between it and the orchestrator.
    """
    healing = healing or FakeHealing()
    monkeypatch.setattr("api.agents.orchestrator.IngestionAgent", FakeIngestion)
    monkeypatch.setattr("api.agents.orchestrator.GenerationAgent", FakeGeneration(generated))
    if execution is not None:
        monkeypatch.setattr("api.agents.orchestrator.ExecutionAgent", execution)
    monkeypatch.setattr("api.agents.orchestrator.SelfHealingAgent", healing)
    return healing
