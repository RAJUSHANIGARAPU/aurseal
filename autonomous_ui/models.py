from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class FailureType(str, Enum):
    LOCATOR = "locator"  # element not found / strict mode violation
    TIMEOUT = "timeout"  # wait timed out before element was ready
    ASSERTION = "assertion"  # AssertionError — value or state mismatch
    NAVIGATION = "navigation"  # page load failure / network error
    UNKNOWN = "unknown"


@dataclass
class FailureAnalysis:
    test_name: str
    failure_type: FailureType
    root_cause: str
    confidence: str  # "high" | "medium" | "low"
    selectors_mentioned: list[str] = field(default_factory=list)
    llm_suggestion: str = ""


@dataclass
class HealingResult:
    test_name: str
    strategy: str  # "locator_patch" | "wait_retry" | "assertion_patch" | "none"
    applied: bool
    details: str
    patched_files: list[Path] = field(default_factory=list)


@dataclass
class FailureBundle:
    test: str
    timestamp: str
    error: str
    stack_trace: str
    screenshot: str  # base64 PNG
    console_errors: list[str]
    failed_requests: list[str]
    dom_snapshot: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> FailureBundle:
        return cls(
            test=data.get("test", ""),
            timestamp=data.get("timestamp", ""),
            error=data.get("error", ""),
            stack_trace=data.get("stackTrace", ""),
            screenshot=data.get("screenshot", ""),
            console_errors=data.get("consoleErrors", []),
            failed_requests=data.get("failedRequests", []),
            dom_snapshot=data.get("domSnapshot", ""),
        )
