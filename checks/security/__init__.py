"""Security checks that can fail: gitleaks (secrets), bandit (SAST), pip-audit (dependencies),
with an allow-list whose entries expire. Entry point: ``checks.security.scan.scan``."""

from checks.security.allowlist import Allowlist
from checks.security.model import SecurityFinding, Severity, ToolRun

__all__ = ["Allowlist", "SecurityFinding", "Severity", "ToolRun"]
