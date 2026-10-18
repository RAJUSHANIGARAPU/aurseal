"""Run gitleaks, bandit and pip-audit, and turn each one's JSON report into findings.

Each runner returns a ``ToolRun``; a tool that is missing, crashes, or (for pip-audit)
cannot reach its vulnerability database returns ``ran=False`` with the reason, never an
empty "clean" run. The parsers are separate so recorded reports can be tested offline.

Verified against gitleaks 8.30.1, bandit 1.9.4 and pip-audit 2.10.1.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from checks.security.model import SecurityFinding, Severity, ToolRun

GITLEAKS_ENV = "GITLEAKS_BIN"
TIMEOUT_S = 900


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def _module_cli(module: str) -> list[str] | None:
    """``[python, -m, module]`` when the module is importable in this interpreter."""
    import importlib.util

    if importlib.util.find_spec(module) is None:
        return None
    return [sys.executable, "-m", module]


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=cwd)


def _version(cmd: list[str]) -> str | None:
    try:
        out = _run(cmd)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = (out.stdout or out.stderr or "").split()
    return next((w for w in words if w[:1].isdigit()), None)


# -- gitleaks ---------------------------------------------------------------------------


def parse_gitleaks(raw: object) -> list[SecurityFinding]:
    if not isinstance(raw, list):
        raise ValueError("a gitleaks report is a JSON array")
    findings = []
    for leak in raw:
        location = f"{leak.get('File')}:{leak.get('StartLine')}"
        commit = str(leak.get("Commit") or "")[:12]
        fingerprint = f"gitleaks:{leak.get('RuleID')}:{leak.get('File')}:{leak.get('StartLine')}"
        findings.append(
            SecurityFinding(
                tool="gitleaks",
                rule_id=str(leak.get("RuleID")),
                severity=Severity.HIGH,
                location=f"{location}@{commit}" if commit else location,
                fingerprint=fingerprint,
                message=str(leak.get("Description") or ""),
            )
        )
    return findings


def run_gitleaks(
    repo: Path, *, config: Path | None = None, report: Path, binary: str | None = None
) -> ToolRun:
    """Scan the git history of ``repo`` (``gitleaks git``); secrets are redacted in output."""
    exe = binary or os.environ.get(GITLEAKS_ENV) or shutil.which("gitleaks")
    if not exe or not (Path(exe).is_file() or shutil.which(exe)):
        return ToolRun("gitleaks", ran=False, reason="gitleaks is not installed")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    cmd = [exe, "git", str(repo), "--no-banner", "--redact", "--log-level", "error"]
    cmd += ["--report-format", "json", "--report-path", str(report), "--exit-code", "1"]
    if config is not None and config.is_file():
        cmd += ["--config", str(config)]
    try:
        proc = _run(cmd)
    except subprocess.TimeoutExpired:
        return ToolRun("gitleaks", ran=False, reason=f"gitleaks did not finish in {TIMEOUT_S}s")
    except OSError as exc:
        return ToolRun("gitleaks", ran=False, reason=f"gitleaks could not start: {exc}")
    version = _version([exe, "version"])
    if proc.returncode not in (0, 1) or not report.is_file():
        return ToolRun(
            "gitleaks",
            ran=False,
            exit_code=proc.returncode,
            version=version,
            reason=f"gitleaks exited {proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}",
        )
    try:
        findings = parse_gitleaks(json.loads(report.read_text(encoding="utf-8") or "[]"))
    except ValueError as exc:
        return ToolRun("gitleaks", ran=False, reason=f"unreadable gitleaks report: {exc}")
    return ToolRun("gitleaks", True, "", proc.returncode, version, findings, report)


# -- bandit -----------------------------------------------------------------------------


def parse_bandit(raw: object, root: Path | None = None) -> list[SecurityFinding]:
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        raise ValueError("a bandit report is an object with a 'results' list")
    findings = []
    for result in raw["results"]:
        path = Path(str(result.get("filename")))
        if root is not None:
            try:
                path = path.resolve().relative_to(root.resolve())
            except ValueError:
                pass
        rel = path.as_posix().removeprefix("./")
        line = int(result.get("line_number") or 0)
        code_lines = str(result.get("code") or "").splitlines()
        flagged = next(
            (c.split(" ", 1)[1] for c in code_lines if c.split(" ", 1)[0] == str(line)),
            code_lines[0] if code_lines else "",
        )
        findings.append(
            SecurityFinding(
                tool="bandit",
                rule_id=str(result.get("test_id")),
                severity=Severity.parse(result.get("issue_severity")),
                location=f"{rel}:{line}",
                fingerprint=f"bandit:{result.get('test_id')}:{rel}:{_short_hash(flagged)}",
                message=str(result.get("issue_text") or ""),
            )
        )
    return findings


def default_bandit_targets(root: Path) -> list[Path]:
    """First-party packages: top-level directories with ``__init__.py``, tests excluded."""
    skip = {"tests", "generated_tests", ".venv", "venv", "reports", "node_modules"}
    return sorted(
        p
        for p in root.iterdir()
        if p.is_dir()
        and p.name not in skip
        and not p.name.startswith(".")
        and (p / "__init__.py").is_file()
    )


def run_bandit(
    paths: Sequence[Path],
    *,
    report: Path,
    root: Path | None = None,
    severity: str = "medium",
    confidence: str = "medium",
) -> ToolRun:
    cli = _module_cli("bandit")
    if cli is None:
        return ToolRun("bandit", ran=False, reason="bandit is not installed")
    if not paths:
        return ToolRun("bandit", ran=False, reason="no paths to scan")
    report.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*cli, "-r", *[str(p) for p in paths], "-f", "json", "-o", str(report), "-q"]
    cmd += ["--severity-level", severity, "--confidence-level", confidence]
    cmd += ["-x", ",".join(f"*/{d}/*" for d in ("tests", ".venv", "__pycache__"))]
    try:
        proc = _run(cmd, cwd=root)
    except subprocess.TimeoutExpired:
        return ToolRun("bandit", ran=False, reason=f"bandit did not finish in {TIMEOUT_S}s")
    version = _version([*cli, "--version"])
    if proc.returncode not in (0, 1) or not report.is_file():
        return ToolRun(
            "bandit",
            ran=False,
            exit_code=proc.returncode,
            reason=f"bandit exited {proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}",
        )
    try:
        raw = json.loads(report.read_text(encoding="utf-8"))
        findings = parse_bandit(raw, root)
    except ValueError as exc:
        return ToolRun("bandit", ran=False, reason=f"unreadable bandit report: {exc}")
    if raw.get("errors"):
        return ToolRun(
            "bandit",
            ran=False,
            exit_code=proc.returncode,
            version=version,
            reason=f"bandit could not scan every file: {raw['errors'][:3]}",
            findings=findings,
        )
    return ToolRun("bandit", True, "", proc.returncode, version, findings, report)


# -- pip-audit --------------------------------------------------------------------------


def parse_pip_audit(raw: object) -> list[SecurityFinding]:
    if not isinstance(raw, dict) or not isinstance(raw.get("dependencies"), list):
        raise ValueError("a pip-audit report is an object with a 'dependencies' list")
    findings: dict[str, SecurityFinding] = {}
    for dep in raw["dependencies"]:
        for vuln in dep.get("vulns") or ():
            fix = ", ".join(vuln.get("fix_versions") or ()) or None
            fingerprint = f"pip-audit:{dep.get('name')}:{vuln.get('id')}"
            findings.setdefault(
                fingerprint,
                SecurityFinding(
                    tool="pip-audit",
                    rule_id=str(vuln.get("id")),
                    severity=Severity.UNKNOWN,
                    location=f"{dep.get('name')}=={dep.get('version')}",
                    fingerprint=fingerprint,
                    message=", ".join(vuln.get("aliases") or ()),
                    fix=f"upgrade to {fix}" if fix else None,
                ),
            )
    return list(findings.values())


def run_pip_audit(lockfile: Path, *, report: Path) -> ToolRun:
    """Audit a fully pinned requirements file against the public vulnerability database.

    Needs network. ``--no-deps --disable-pip`` audits exactly the pinned set without
    building an environment.
    """
    cli = _module_cli("pip_audit")
    if cli is None:
        return ToolRun("pip-audit", ran=False, reason="pip-audit is not installed")
    if not lockfile.is_file():
        return ToolRun("pip-audit", ran=False, reason=f"no lock file at {lockfile}")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    cmd = [*cli, "-r", str(lockfile), "--no-deps", "--disable-pip", "--format", "json"]
    cmd += ["--progress-spinner", "off", "-o", str(report)]
    try:
        proc = _run(cmd)
    except subprocess.TimeoutExpired:
        return ToolRun("pip-audit", ran=False, reason=f"pip-audit did not finish in {TIMEOUT_S}s")
    version = _version([*cli, "--version"])
    if proc.returncode not in (0, 1) or not report.is_file():
        return ToolRun(
            "pip-audit",
            ran=False,
            exit_code=proc.returncode,
            version=version,
            reason=f"pip-audit exited {proc.returncode}: {(proc.stderr or proc.stdout)[-300:]}",
        )
    try:
        findings = parse_pip_audit(json.loads(report.read_text(encoding="utf-8")))
    except ValueError as exc:
        return ToolRun("pip-audit", ran=False, reason=f"unreadable pip-audit report: {exc}")
    return ToolRun("pip-audit", True, "", proc.returncode, version, findings, report)
