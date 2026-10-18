"""
Allure's Environment panel (``environment.properties``) and Executor row (``executor.json``).

A report that does not say which environment, browser, build and commit it came
from cannot be compared with another report, and a red cannot be attributed to a
change. These two files are written once per session, before any test runs:

* ``environment.properties`` — environment, base URL, browser(s), headed or not,
  Playwright / Python / OS versions, the **resolved** auth mode (resolved, not read
  from one file: an ``AUTH_MODE`` export overrides the file, and the panel exists
  to say how the run was actually configured), git branch and commit, CI or local,
  run id and timestamp, plus any extra rows a caller supplies (checkout provenance,
  the measured deployed version);
* ``executor.json`` — GitHub Actions or Jenkins with a link to the build, else
  ``local @ <host>``.

Every value is read defensively; writing report metadata must never abort a run.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from core.logger import logger


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "n/a"


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else "unknown"


def is_ci(environ: Mapping[str, str]) -> bool:
    return any(environ.get(k) for k in ("CI", "GITHUB_ACTIONS", "BUILD_NUMBER", "JENKINS_URL"))


def git_branch(environ: Mapping[str, str]) -> str:
    for key in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GIT_BRANCH", "BRANCH_NAME"):
        if environ.get(key):
            return environ[key]
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def git_commit(environ: Mapping[str, str]) -> str:
    for key in ("GITHUB_SHA", "GIT_COMMIT"):
        if environ.get(key):
            return environ[key][:8]
    return _git("rev-parse", "--short=8", "HEAD")


def environment_rows(
    *,
    env: str,
    base_url: str,
    browsers: list[str],
    headed: bool,
    auth_mode: str,
    run_id: str,
    extra: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    rows = {
        "Environment": env,
        "Base.URL": base_url or "n/a",
        "Browser": ", ".join(browsers) or "chromium",
        "Headless": str(not headed).lower(),
        "Playwright.Version": _version("playwright"),
        "Python.Version": platform.python_version(),
        "OS": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "Auth.Mode": auth_mode,
        "Git.Branch": git_branch(environ),
        "Git.Commit": git_commit(environ),
        "Run.Mode": "CI" if is_ci(environ) else "Local",
        "Run.Id": run_id,
        "Run.Timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # The external configuration the binding tests ran against (gate.external_config).
    ext_commit = (environ.get("EXT_CONFIG_COMMIT") or "").strip()
    if ext_commit:
        rows["External.Config.Commit"] = ext_commit
    rows.update(extra or {})
    return rows


def _escape(text: str) -> str:
    """Java ``.properties`` escaping for keys and values."""
    out = str(text).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    return out.replace("=", "\\=").replace(":", "\\:")


def properties_text(rows: Mapping[str, str]) -> str:
    lines = []
    for key, value in rows.items():
        escaped_key = _escape(key).replace(" ", "\\ ")
        lines.append(escaped_key + "=" + _escape(value) + "\n")
    return "".join(lines)


def executor_info(report_name: str, environ: Mapping[str, str] | None = None) -> dict:
    environ = os.environ if environ is None else environ
    if environ.get("GITHUB_RUN_ID"):
        server = environ.get("GITHUB_SERVER_URL", "")
        repo = environ.get("GITHUB_REPOSITORY", "")
        url = f"{server}/{repo}/actions/runs/{environ['GITHUB_RUN_ID']}" if server and repo else ""
        number = environ.get("GITHUB_RUN_NUMBER", environ["GITHUB_RUN_ID"])
        info = {
            "name": "GitHub Actions",
            "type": "github",
            "url": url,
            "buildOrder": int(number) if str(number).isdigit() else None,
            "buildName": f"{environ.get('GITHUB_WORKFLOW', 'workflow')} #{number}",
            "buildUrl": url,
            "reportName": report_name,
        }
    elif environ.get("BUILD_NUMBER"):
        number = environ["BUILD_NUMBER"]
        info = {
            "name": "Jenkins",
            "type": "jenkins",
            "url": environ.get("BUILD_URL", ""),
            "buildOrder": int(number) if number.isdigit() else None,
            "buildName": f"{environ.get('JOB_NAME') or 'build'} #{number}",
            "buildUrl": environ.get("BUILD_URL", ""),
            "reportName": report_name,
        }
    else:
        try:
            host = socket.gethostname()
        except OSError:
            host = "localhost"
        info = {
            "name": "Local",
            "type": "local",
            "buildName": f"local @ {host}",
            "reportName": report_name,
        }
    return {k: v for k, v in info.items() if v is not None}


def write_environment(
    results_dir: str | Path, rows: Mapping[str, str], executor: dict
) -> list[Path]:
    """Write both files; logs and returns [] instead of raising on any failure."""
    try:
        directory = Path(results_dir)
        directory.mkdir(parents=True, exist_ok=True)
        env_path = directory / "environment.properties"
        env_path.write_text(properties_text(rows), encoding="utf-8")
        exec_path = directory / "executor.json"
        exec_path.write_text(json.dumps(executor, indent=2), encoding="utf-8")
        return [env_path, exec_path]
    except Exception as exc:  # noqa: BLE001 - report metadata never aborts a run
        logger.warning("[allure] writing report metadata failed: %s", exc)
        return []
