"""
Run the suite (or a selection) and write a health report with a release-gate verdict.

Gate:

* **Block** - any failure or error (or nothing ran at all);
* **Warn** - no failures, but at least one heal was applied during the run: locators
  changed and want a review before release;
* **Pass** - no failures and no applied heal. When healing was not measured the gate
  can still pass, and the report says healing was not measured.

    python -m tools.slash.health [--env qa] [--out reports/health] [-- <pytest args>]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.slash import healing, junit, reach

BLOCK, WARN, PASS = "BLOCK", "WARN", "PASS"
_RERUN = re.compile(r"^(\S+::\S+)\s+RERUN\b|RERUN\s+(\S+::\S+)", re.M)


def gate(tests: junit.JUnitSummary, heals: healing.HealingWindow) -> str:
    if not tests.measured or tests.total == 0 or tests.failed:
        return BLOCK
    if heals.measured and heals.applied:
        return WARN
    return PASS


def retried(console: str) -> list[str]:
    found = []
    for a, b in _RERUN.findall(console):
        found.append(a or b)
    return list(dict.fromkeys(found))


@dataclass
class RunInfo:
    started: datetime
    duration_s: float
    env: str
    branch: str
    console: str


def render(info: RunInfo, tests: junit.JUnitSummary, heals: healing.HealingWindow) -> str:
    verdict = gate(tests, heals)
    healed = str(len(heals.applied)) if heals.measured else "not measured"
    out = [
        f"# Test health - {info.started:%Y-%m-%d %H:%M} UTC",
        "",
        f"Environment: {info.env} | Branch: {info.branch} | Duration: {info.duration_s:.0f} s",
        "",
        "## Summary",
        "",
        "| Passed | Failed | Skipped | Healed |",
        "|---|---|---|---|",
    ]
    if tests.measured:
        out.append(f"| {tests.passed} | {tests.failed} | {tests.skipped} | {healed} |")
    else:
        out.append(f"| missing | missing | missing | {healed} |")
    out += ["", "## Failed tests", ""]
    out += [
        f"- `{c.node}` ({c.kind}): {c.message.splitlines()[0] if c.message else ''}"
        for c in tests.cases
    ] or ["(none)"]
    out += [f"- unreadable report: {name}" for name in tests.unreadable]
    out += ["", "## Healing events", ""]
    if heals.measured:
        out.append(healing.describe(heals, verbose=True).removeprefix("Healing: "))
    else:
        out.append(
            f"Not measured: {heals.reason}. An ordinary pytest run does not heal; "
            "only the healing orchestrator writes this log."
        )
    out += ["", "## Flakiness", ""]
    reruns = retried(info.console)
    out.append("Retried: " + (", ".join(f"`{t}`" for t in reruns) if reruns else "none"))
    out.append("Slowest:")
    out += [f"- `{name}` {seconds:.2f} s" for name, seconds in tests.slowest(5)] or [
        "- (no timings)"
    ]
    out += ["", "## Gate", "", f"**{verdict}**"]
    if verdict == PASS and not heals.measured:
        out.append("Healing was not measured for this run.")
    if verdict == WARN:
        out.append("Heals were applied: review the changed locators before release.")
    if not tests.measured:
        out.append("No JUnit results were produced, so nothing is known to have passed.")
    return "\n".join(out) + "\n"


def save(report: str, out_dir: Path, started: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"test-health-{started:%Y%m%d-%H%M%S}.md"
    path.write_text(report, encoding="utf-8")
    return path


def run(
    pytest_args: list[str],
    *,
    env: str,
    out_dir: Path,
    log: Path = healing.DEFAULT_LOG,
    junit_path: Path = Path("reports/junit-health.xml"),
    probe: Callable[[], tuple[bool, str]] | None = None,
    runner: Callable[[list[str]], str] | None = None,
    branch: str | None = None,
) -> tuple[int, str]:
    ok, detail = (probe or (lambda: reach.probe(reach.base_url(env), 10)))()
    if not ok:
        return 1, f"stop: the application is down - {detail}"
    started = datetime.now(tz=timezone.utc)
    clock = time.monotonic()
    cmd = [sys.executable, "-m", "pytest", *pytest_args, f"--junitxml={junit_path}", "--env", env]
    if runner is None:
        with tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False) as handle:
            subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT, check=False)
            handle.seek(0)
            console = handle.read()
    else:
        console = runner(cmd)
    print("\n".join(console.splitlines()[-30:]))
    if branch is None:
        branch = (
            subprocess.run(
                ["git", "branch", "--show-current"], capture_output=True, text=True
            ).stdout.strip()
            or "?"
        )
    info = RunInfo(started, time.monotonic() - clock, env, branch, console)
    tests = junit.aggregate([junit_path] if junit_path.exists() else [])
    heals = healing.read_window(log, started)
    report = render(info, tests, heals)
    path = save(report, out_dir, started)
    return (0 if gate(tests, heals) != BLOCK else 1), f"{report}\nsaved: {path}"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        extra = argv[argv.index("--") + 1 :]
        argv = argv[: argv.index("--")]
    parser = argparse.ArgumentParser(description="Suite health report with a release gate.")
    parser.add_argument("--env", default="qa")
    parser.add_argument("--out", type=Path, default=Path("reports/health"))
    parser.add_argument("--log", type=Path, default=healing.DEFAULT_LOG)
    args = parser.parse_args(argv)
    code, text = run(extra, env=args.env, out_dir=args.out, log=args.log)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
