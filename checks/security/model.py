"""One finding shape for every security tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: object) -> Severity:
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class SecurityFinding:
    tool: str
    rule_id: str
    severity: Severity
    location: str
    fingerprint: str
    message: str = ""
    fix: str | None = None

    def summary(self) -> dict:
        out = {
            "tool": self.tool,
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "location": self.location,
            "fingerprint": self.fingerprint,
        }
        if self.fix:
            out["fix"] = self.fix
        return out


@dataclass
class ToolRun:
    """What one tool did. ``ran=False`` means it gave no answer (missing, crashed, offline)."""

    tool: str
    ran: bool
    reason: str = ""
    exit_code: int | None = None
    version: str | None = None
    findings: list[SecurityFinding] = field(default_factory=list)
    report: Path | None = None
