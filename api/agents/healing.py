"""SelfHealingAgent: apply rule-based and LLM-based fixes to failing tests."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.agents.analysis import FailureAnalysis
    from api.llm.base import BaseLLMClient

from api.agents.analysis import FailureCategory

logger = logging.getLogger(__name__)

_TIMEOUT_PATTERN = re.compile(r"(timeout=)(\d+)")
_STATUS_PATTERN = re.compile(r"(expected_status=)(\d+)")


@dataclass
class HealingResult:
    """Outcome of a healing attempt."""

    fixed: bool
    changes_made: list[str]
    new_file_path: Path | None


class SelfHealingAgent:
    """Applies rule-based and optional LLM-based fixes to generated test files."""

    def __init__(
        self,
        llm: BaseLLMClient | None = None,
        relax_status_assertion: bool = False,
    ) -> None:
        self._llm = llm
        self._relax_status = relax_status_assertion

    def heal(
        self,
        analyses: list[FailureAnalysis],
        test_file_path: Path,
    ) -> HealingResult:
        """Attempt to fix the test file based on failure analyses.

        Rule-based fixes are applied first; LLM-based fix follows if available.
        Returns HealingResult describing what was changed.
        """
        if not test_file_path.exists():
            logger.warning("Test file not found for healing: %s", test_file_path)
            return HealingResult(fixed=False, changes_made=[], new_file_path=None)

        original_code = test_file_path.read_text(encoding="utf-8")
        code = original_code
        changes: list[str] = []

        for analysis in analyses:
            code, applied = self._apply_rule_fix(code, analysis)
            changes.extend(applied)

        # LLM-based fix applied once per file for all failures together
        if self._llm and analyses:
            code, llm_changes = self._apply_llm_fix(code, analyses, test_file_path)
            changes.extend(llm_changes)

        if code == original_code:
            logger.info("No healing changes for %s", test_file_path)
            return HealingResult(fixed=False, changes_made=[], new_file_path=None)

        test_file_path.write_text(code, encoding="utf-8")
        logger.info("Healed %s with %d change(s)", test_file_path, len(changes))
        return HealingResult(fixed=True, changes_made=changes, new_file_path=test_file_path)

    def _apply_rule_fix(self, code: str, analysis: FailureAnalysis) -> tuple[str, list[str]]:
        changes: list[str] = []
        category = analysis.category

        if category == FailureCategory.TIMEOUT_ERROR:

            def bump_timeout(m: re.Match) -> str:
                current = int(m.group(2))
                new_val = min(current * 2, 120)
                return f"{m.group(1)}{new_val}"

            new_code = _TIMEOUT_PATTERN.sub(bump_timeout, code)
            if new_code != code:
                changes.append("Doubled request timeout (TIMEOUT_ERROR rule)")
                code = new_code

        if category == FailureCategory.ASSERTION_ERROR and self._relax_status:
            # Relax a strict 2xx assertion to check response is not 5xx
            def relax_status(m: re.Match) -> str:
                status = int(m.group(2))
                if status in (200, 201, 202, 204):
                    return f"{m.group(1)}None"
                return m.group(0)

            new_code = _STATUS_PATTERN.sub(relax_status, code)
            if new_code != code:
                changes.append("Relaxed expected_status assertion (ASSERTION_ERROR rule)")
                code = new_code

        if category == FailureCategory.DATA_ERROR:
            # Add a comment hint for regenerating data — actual regen happens at orchestrator level
            marker = "# DATA_ERROR: regenerate test data on next run\n"
            if marker not in code:
                code = marker + code
                changes.append("Marked file for data regeneration (DATA_ERROR rule)")

        return code, changes

    def _apply_llm_fix(
        self,
        code: str,
        analyses: list[FailureAnalysis],
        file_path: Path,
    ) -> tuple[str, list[str]]:
        if not self._llm:
            return code, []

        failure_summary = "\n".join(
            f"- [{a.category.value}] {a.test_name}: {a.root_cause}" for a in analyses
        )
        suggested = "\n".join(f"- {a.suggested_fix}" for a in analyses if a.suggested_fix)

        prompt = (
            f"You are an API test automation expert.\n"
            f"Fix the following pytest test file to resolve the failures listed below.\n\n"
            f"FILE: {file_path.name}\n"
            f"```python\n{code}\n```\n\n"
            f"FAILURES:\n{failure_summary}\n\n"
            f"SUGGESTED FIXES:\n{suggested}\n\n"
            f"Rules:\n"
            f"- Return ONLY the complete fixed Python file content\n"
            f"- No explanation, no markdown fences, no extra text\n"
            f"- Keep all existing test functions; only fix what is broken\n"
            f"- Do not change import paths\n"
        )

        fixed = self._llm.complete(prompt, max_tokens=4096) if self._llm else ""
        if not fixed or fixed.startswith("Claude API error"):
            return code, []

        # Strip markdown fences if model included them
        fixed = re.sub(r"^```python\n?", "", fixed.strip())
        fixed = re.sub(r"\n?```$", "", fixed.strip())

        if fixed == code:
            return code, []

        return fixed, ["Applied LLM-based fix"]
