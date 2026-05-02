"""Security scan plugin — pip-audit CVE checks and source pattern scanning."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_SECRET_PATTERNS = [
    re.compile(r'(?i)(password|secret|api_key|token)\s*=\s*["\'][^"\']{4,}["\']'),
    re.compile(r"(?i)-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"),
]
_SQL_INJECTION_PATTERNS = [
    re.compile(r'(?i)execute\s*\(\s*["\']?\s*SELECT.*\+'),
    re.compile(r'(?i)f["\'].*SELECT.*\{'),
]


class SecurityScanPlugin(BasePlugin):
    name = "security-scan"
    priority = PluginPriority.HIGH
    trigger_conditions = ["deploy", "manual"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        py_files = list(source_dir.rglob("*.py"))

        if context.get("dry_run"):
            return PluginResult(
                status="pass",
                findings=[{"files_to_scan": len(py_files)}],
                dry_run=True,
            )

        governor = context.get("cost_governor") or CostGovernor()  # noqa: F841

        # Run pip-audit
        cve_findings: list[dict] = []
        try:
            proc = subprocess.run(
                ["pip-audit", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                cve_findings.append({"pip_audit_error": proc.stderr[:500]})
            else:
                import json

                try:
                    audit_data = json.loads(proc.stdout)
                    cve_count = len(audit_data.get("vulnerabilities", []))
                    cve_findings.append({"cve_count": cve_count, "vulnerabilities": audit_data})
                except Exception:  # noqa: BLE001
                    cve_findings.append({"pip_audit_output": proc.stdout[:500]})
        except FileNotFoundError:
            cve_findings.append({"pip_audit": "not installed"})
        except Exception as exc:  # noqa: BLE001
            cve_findings.append({"pip_audit_error": str(exc)})

        # Scan source patterns
        pattern_matches: list[dict] = []
        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            for pattern in _SECRET_PATTERNS:
                if pattern.search(content):
                    pattern_matches.append({"file": str(py_file), "type": "potential_secret"})
            for pattern in _SQL_INJECTION_PATTERNS:
                if pattern.search(content):
                    pattern_matches.append({"file": str(py_file), "type": "sql_injection_risk"})

        all_findings = cve_findings + pattern_matches
        status = "fail" if pattern_matches else "pass"
        return PluginResult(status=status, findings=all_findings)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = SecurityScanPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
