"""Run the requested security tools and reach one verdict.

* any blocking finding (not allow-listed, or allow-listed by an expired entry), or any
  expired allow-list entry ⇒ ``fail``;
* otherwise, any requested tool that gave no answer ⇒ ``unknown``;
* every requested tool ran and nothing blocks ⇒ ``pass``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from checks._outcome import CheckOutcome
from checks._sink import REPO_ROOT, MetricSink, metric_row
from checks.security import tools
from checks.security.allowlist import Allowlist
from checks.security.model import ToolRun

KIND = "security"
TOOLS = ("gitleaks", "bandit", "pip-audit")
FINDING_TABLE = "metric_security_finding"
SCAN_TABLE = "metric_security_scan"
DEFAULT_ALLOWLIST = REPO_ROOT / "security" / "allowlist.yml"
DEFAULT_LOCKFILE = REPO_ROOT / "requirements-lock.txt"
DEFAULT_GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"
REPORT_DIR = REPO_ROOT / "reports" / "security"


def run_tool(
    name: str,
    *,
    root: Path,
    out_dir: Path,
    lockfile: Path,
    bandit_paths: Sequence[Path] | None,
    gitleaks_config: Path | None,
) -> ToolRun:
    if name == "gitleaks":
        return tools.run_gitleaks(root, config=gitleaks_config, report=out_dir / "gitleaks.json")
    if name == "bandit":
        paths = list(bandit_paths) if bandit_paths else tools.default_bandit_targets(root)
        return tools.run_bandit(paths, report=out_dir / "bandit.json", root=root)
    if name == "pip-audit":
        return tools.run_pip_audit(lockfile, report=out_dir / "pip-audit.json")
    return ToolRun(name, ran=False, reason=f"unknown tool {name!r} (known: {list(TOOLS)})")


def scan(
    requested: Sequence[str] = TOOLS,
    *,
    root: Path = REPO_ROOT,
    out_dir: Path = REPORT_DIR,
    lockfile: Path = DEFAULT_LOCKFILE,
    allowlist: Allowlist | None = None,
    bandit_paths: Sequence[Path] | None = None,
    gitleaks_config: Path | None = DEFAULT_GITLEAKS_CONFIG,
    today: date | None = None,
    sink: MetricSink | None = None,
    runner: Callable[..., ToolRun] | None = None,
) -> CheckOutcome:
    if not requested:
        return CheckOutcome.unknown(KIND, "no tools were requested")
    if allowlist is None:
        allowlist = Allowlist.load(DEFAULT_ALLOWLIST)
    today = today or date.today()
    runner = runner or run_tool
    runs = [
        runner(
            name,
            root=root,
            out_dir=out_dir,
            lockfile=lockfile,
            bandit_paths=bandit_paths,
            gitleaks_config=gitleaks_config,
        )
        for name in requested
    ]
    findings = [f for r in runs if r.ran for f in r.findings]
    judged = allowlist.apply(findings, today)
    not_run = [r for r in runs if not r.ran]
    summary = {
        "tools": {
            r.tool: {"ran": r.ran, "findings": len(r.findings), "reason": r.reason or None}
            for r in runs
        },
        "blocking": [f.summary() for f in judged.blocking],
        "allowlisted": [
            {**f.summary(), "reason": e.reason, "expires": e.expires.isoformat()}
            for f, e in judged.allowlisted
        ],
        "expired_entries": [e.fingerprint for e in judged.expired],
        "unused_entries": [e.fingerprint for e in judged.unused],
    }
    if sink is not None:
        _record(runs, judged, sink)
    metrics = {"blocking": len(judged.blocking), "allowlisted": len(judged.allowlisted)}
    problems = []
    if judged.blocking:
        by_tool: dict[str, int] = {}
        for f in judged.blocking:
            by_tool[f.tool] = by_tool.get(f.tool, 0) + 1
        problems.append(f"blocking findings {by_tool}")
    if judged.expired:
        problems.append(f"{len(judged.expired)} allow-list entr(y/ies) expired")
    if problems:
        if not_run:
            problems.append(f"and did not run: {[r.tool for r in not_run]}")
        return CheckOutcome.breached(KIND, "; ".join(problems), metrics=metrics, findings=[summary])
    if not_run:
        return CheckOutcome.unknown(
            KIND,
            "; ".join(f"{r.tool}: {r.reason}" for r in not_run),
            metrics=metrics,
            findings=[summary],
        )
    return CheckOutcome.ok(
        KIND,
        f"{', '.join(requested)} ran; nothing blocking",
        metrics=metrics,
        findings=[summary],
    )


def _record(runs: list[ToolRun], judged, sink: MetricSink) -> None:
    allowed = {f.fingerprint: e for f, e in judged.allowlisted}
    blocking = {f.fingerprint for f in judged.blocking}
    sink.write(
        SCAN_TABLE,
        [
            metric_row(
                tool=r.tool,
                tool_version=r.version,
                findings=len(r.findings),
                blocking=sum(1 for f in r.findings if f.fingerprint in blocking),
                exit_code=r.exit_code,
            )
            for r in runs
            if r.ran
        ],
    )
    sink.write(
        FINDING_TABLE,
        [
            metric_row(
                tool=f.tool,
                rule_id=f.rule_id,
                severity=f.severity.value,
                location=f.location,
                fingerprint=f.fingerprint,
                allowlisted=int(f.fingerprint in allowed),
                allowlist_expires=(
                    allowed[f.fingerprint].expires.isoformat() if f.fingerprint in allowed else None
                ),
            )
            for r in runs
            if r.ran
            for f in r.findings
        ],
    )
