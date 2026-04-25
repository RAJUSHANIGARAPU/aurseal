"""AnalysisAgent: categorise test failures and optionally diagnose with LLM."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.agents.execution import ExecutionResult
    from api.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class FailureCategory(str, Enum):
    ASSERTION_ERROR = "ASSERTION_ERROR"
    DATA_ERROR = "DATA_ERROR"
    API_ERROR = "API_ERROR"
    ENV_ERROR = "ENV_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class FailureAnalysis:
    """Diagnosis of a single test failure."""

    test_name: str
    category: FailureCategory
    root_cause: str
    suggested_fix: str
    raw_message: str = ""
    llm_diagnosis: dict = field(default_factory=dict)


# Pattern-based categorisation rules (order matters — first match wins)
_PATTERNS: list[tuple[re.Pattern, FailureCategory, str]] = [
    (
        re.compile(r"(Timeout|timed out|ReadTimeout|ConnectTimeout)", re.I),
        FailureCategory.TIMEOUT_ERROR,
        "Request timed out",
    ),
    (
        re.compile(r"(ConnectionError|ConnectionRefused|Cannot connect|Failed to establish)", re.I),
        FailureCategory.ENV_ERROR,
        "Cannot reach the target server",
    ),
    (
        re.compile(r"(AssertionError|assert\s+)", re.I),
        FailureCategory.ASSERTION_ERROR,
        "Assertion failed in test",
    ),
    (
        re.compile(r"(4\d\d|Bad Request|Unauthorized|Forbidden|Not Found|Unprocessable)", re.I),
        FailureCategory.API_ERROR,
        "API returned a 4xx client error",
    ),
    (
        re.compile(r"(5\d\d|Internal Server Error|Bad Gateway|Service Unavailable)", re.I),
        FailureCategory.API_ERROR,
        "API returned a 5xx server error",
    ),
    (
        re.compile(r"(JSON|json|schema|parse|decode)", re.I),
        FailureCategory.DATA_ERROR,
        "Response data does not match expected format",
    ),
]

_SUGGESTED_FIXES: dict[FailureCategory, str] = {
    FailureCategory.TIMEOUT_ERROR: "Increase request timeout or check server availability",
    FailureCategory.ENV_ERROR: "Verify base URL and network connectivity",
    FailureCategory.ASSERTION_ERROR: "Review expected status code or response schema",
    FailureCategory.API_ERROR: "Check request payload, authentication headers, or endpoint path",
    FailureCategory.DATA_ERROR: "Regenerate with fresh test data or update JSON schema",
    FailureCategory.UNKNOWN: "Inspect raw test output for clues",
}


class AnalysisAgent:
    """Categorises failures using pattern matching, optionally using LLM for deeper diagnosis."""

    def __init__(self, llm: BaseLLMClient | None = None) -> None:
        self._llm = llm

    def analyze(self, result: ExecutionResult) -> list[FailureAnalysis]:
        """Analyse all failures from an ExecutionResult.

        Returns one FailureAnalysis per failed/errored test.
        """
        analyses: list[FailureAnalysis] = []
        for detail in result.failure_details:
            analysis = self._analyze_one(detail)
            analyses.append(analysis)

        if not result.failure_details and result.failed > 0:
            # We have failure counts but no details — parse raw output
            for i in range(result.failed):
                analyses.append(
                    FailureAnalysis(
                        test_name=f"unknown_test_{i + 1}",
                        category=FailureCategory.UNKNOWN,
                        root_cause="No failure detail available",
                        suggested_fix=_SUGGESTED_FIXES[FailureCategory.UNKNOWN],
                        raw_message=result.raw_output[:500],
                    )
                )

        logger.info("Analyzed %d failures", len(analyses))
        return analyses

    def _analyze_one(self, detail: dict) -> FailureAnalysis:
        test_name = detail.get("test_name", "unknown")
        message = str(detail.get("message", ""))

        category = self._categorize(message)
        suggested_fix = _SUGGESTED_FIXES[category]

        llm_diagnosis: dict = {}
        if self._llm:
            llm_diagnosis = self._llm_diagnose(test_name, message, category)

        return FailureAnalysis(
            test_name=test_name,
            category=category,
            root_cause=self._extract_root_cause(message, category),
            suggested_fix=llm_diagnosis.get("suggested_fix", suggested_fix),
            raw_message=message,
            llm_diagnosis=llm_diagnosis,
        )

    @staticmethod
    def _categorize(message: str) -> FailureCategory:
        for pattern, category, _ in _PATTERNS:
            if pattern.search(message):
                return category
        return FailureCategory.UNKNOWN

    @staticmethod
    def _extract_root_cause(message: str, category: FailureCategory) -> str:
        lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
        if lines:
            return lines[0][:200]
        return category.value

    def _llm_diagnose(
        self,
        test_name: str,
        message: str,
        category: FailureCategory,
    ) -> dict:
        prompt = (
            f"Diagnose this API test failure and return a JSON object with keys: "
            f"root_cause (string), suggested_fix (string), severity (low|medium|high).\n\n"
            f"Test name: {test_name}\n"
            f"Category: {category.value}\n"
            f"Failure message:\n{message[:1000]}\n\n"
            f"Return only the JSON object, no markdown fences."
        )
        raw = self._llm.complete(prompt, max_tokens=512) if self._llm else ""
        if not raw or raw.startswith("Claude API error"):
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_response": raw}
