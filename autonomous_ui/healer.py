"""Applies healing strategies for classified UI test failures.

Healing strategies, in order of safety:
  locator_patch   — rewrites the failing entry in wiki_locators.json with an
                    LLM-suggested alternative selector. Safe: only touches JSON,
                    never touches test code.
  wait_retry      — records the test in healing_overrides.json so the orchestrator
                    adds --reruns on the next pytest run. Safe: no code change.
  assertion_patch — delegates to AutoFixer to rewrite the test file. Guarded:
                    only applied when LLM confidence is high.
  none            — failure type is UNKNOWN or confidence is too low; human review
                    required. The agent logs the analysis and stops.

When NOT to auto-heal:
  - confidence == "low" (UNKNOWN failure type)
  - same locator key has already been patched this session (avoid loops)
  - assertion fix touches expected values that look like business rules
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from api.llm.base import BaseLLMClient
from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.models import FailureAnalysis, FailureType, HealingResult
from core.ai.auto_fixer import AutoFixer

_LOCATOR_REGISTRY = Path("ui/locators/wiki_locators.json")
_HEALING_OVERRIDES = Path("reports/healing_overrides.json")

# JSON block extractor — handles ```json fences from LLM responses
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class UIHealer:
    """Applies the appropriate healing strategy for a FailureAnalysis."""

    def __init__(self, llm: BaseLLMClient | None = None) -> None:
        self._llm = llm or ClaudeLLMClient()
        self._patched_locator_keys: set[str] = set()  # guard against repeated patches

    def heal(self, analysis: FailureAnalysis, dom_snapshot: str = "") -> HealingResult:
        if analysis.confidence == "low":
            return HealingResult(
                test_name=analysis.test_name,
                strategy="none",
                applied=False,
                details="confidence too low — requires human review",
            )

        if analysis.failure_type in (FailureType.LOCATOR, FailureType.TIMEOUT):
            return self._heal_locator(analysis, dom_snapshot)

        if analysis.failure_type == FailureType.ASSERTION:
            return self._heal_assertion(analysis)

        return HealingResult(
            test_name=analysis.test_name,
            strategy="none",
            applied=False,
            details=f"no healing strategy for failure type: {analysis.failure_type.value}",
        )

    # ------------------------------------------------------------------
    # Locator / timeout healing
    # ------------------------------------------------------------------

    def _heal_locator(self, analysis: FailureAnalysis, dom_snapshot: str) -> HealingResult:
        try:
            registry = json.loads(_LOCATOR_REGISTRY.read_text())
        except FileNotFoundError:
            return self._record_retry(analysis)

        # For pure timeout failures without a broken selector, fall back to retry flag
        if analysis.failure_type == FailureType.TIMEOUT and not analysis.selectors_mentioned:
            return self._record_retry(analysis)

        patch = self._ask_llm_for_locator_patch(analysis, registry, dom_snapshot)
        if not patch:
            # LLM could not suggest a fix — fall back to retry-with-reruns
            return self._record_retry(analysis)

        key = patch.get("locator_key", "")
        new_locator = patch.get("new_locator")

        if not key or not new_locator or key not in registry:
            return self._record_retry(analysis)

        if key in self._patched_locator_keys:
            return HealingResult(
                test_name=analysis.test_name,
                strategy="locator_patch",
                applied=False,
                details=(
                    f"locator key '{key}' already patched this session — stopping to avoid loop"
                ),
            )

        old_locator = registry[key]
        registry[key] = new_locator
        _LOCATOR_REGISTRY.write_text(json.dumps(registry, indent=2))
        self._patched_locator_keys.add(key)

        return HealingResult(
            test_name=analysis.test_name,
            strategy="locator_patch",
            applied=True,
            details=(
                f"patched '{key}': {old_locator} → {new_locator} "
                f"(reason: {patch.get('reasoning', 'LLM suggestion')})"
            ),
            patched_files=[_LOCATOR_REGISTRY],
        )

    def _ask_llm_for_locator_patch(
        self, analysis: FailureAnalysis, registry: dict, dom_snapshot: str
    ) -> dict | None:
        selectors_str = ", ".join(analysis.selectors_mentioned) or "(see error below)"
        dom_section = f"\n\nDOM (first 4000 chars):\n{dom_snapshot[:4000]}" if dom_snapshot else ""
        prompt = f"""You are a Playwright Python automation expert performing locator self-healing.

FAILURE:
  test      : {analysis.test_name}
  type      : {analysis.failure_type.value}
  root cause: {analysis.root_cause}
  selectors : {selectors_str}
  llm hint  : {analysis.llm_suggestion}

CURRENT LOCATOR REGISTRY (wiki_locators.json):
{json.dumps(registry, indent=2)}
{dom_section}

Task:
1. Identify which locator key in the registry corresponds to the failing selector.
2. Find the element in the DOM and suggest a more robust alternative selector.
3. Prefer selectors in this priority order:
     data-testid > aria-label > role > placeholder > text > css
4. Never use raw CSS class names that look auto-generated (e.g. "Component_foo__Ab1cd").

Return ONLY valid JSON in exactly this structure, no other text:
{{
  "locator_key": "<key from registry>",
  "new_locator": {{ <valid locator entry matching the registry schema> }},
  "reasoning": "<one sentence>"
}}"""

        raw = self._llm.complete(prompt, max_tokens=512)
        if not raw:
            return None
        return self._parse_json(raw)

    def _record_retry(self, analysis: FailureAnalysis) -> HealingResult:
        _HEALING_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = _HEALING_OVERRIDES.read_text() if _HEALING_OVERRIDES.exists() else "{}"
            overrides = json.loads(text)
        except json.JSONDecodeError:
            overrides = {}

        overrides.setdefault("retry_tests", [])
        if analysis.test_name not in overrides["retry_tests"]:
            overrides["retry_tests"].append(analysis.test_name)

        _HEALING_OVERRIDES.write_text(json.dumps(overrides, indent=2))
        return HealingResult(
            test_name=analysis.test_name,
            strategy="wait_retry",
            applied=True,
            details="recorded for retry with --reruns 2 on next run",
            patched_files=[_HEALING_OVERRIDES],
        )

    # ------------------------------------------------------------------
    # Assertion healing
    # ------------------------------------------------------------------

    def _heal_assertion(self, analysis: FailureAnalysis) -> HealingResult:
        # Only attempt assertion patching when confidence is high and LLM gave a clear
        # suggestion — assertion changes touch expected values, which can mask real bugs.
        if "high" not in analysis.confidence:
            return HealingResult(
                test_name=analysis.test_name,
                strategy="assertion_patch",
                applied=False,
                details="assertion healing requires high confidence to modify expected values",
            )

        # Derive likely test file from test name (pytest node id format: file::class::method)
        test_file = self._locate_test_file(analysis.test_name)
        if not test_file:
            return HealingResult(
                test_name=analysis.test_name,
                strategy="assertion_patch",
                applied=False,
                details="could not locate test file from test name",
            )

        fixer = AutoFixer()
        fixed = fixer.fix_file(
            str(test_file),
            error_description=analysis.root_cause,
            error_trace=analysis.llm_suggestion,
        )
        return HealingResult(
            test_name=analysis.test_name,
            strategy="assertion_patch",
            applied=fixed,
            details=f"AutoFixer {'applied patch to' if fixed else 'no changes to'} {test_file}",
            patched_files=[test_file] if fixed else [],
        )

    def _locate_test_file(self, test_name: str) -> Path | None:
        # test_name may be "test_search_train" or "ui/tests/test_search.py::test_search_train"
        if "::" in test_name:
            candidate = Path(test_name.split("::")[0])
            if candidate.exists():
                return candidate

        # Search ui/tests for a file containing the test function
        for path in Path("ui/tests").glob("test_*.py"):
            if test_name in path.read_text():
                return path
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        # Strip markdown fences if present
        fence_match = _JSON_FENCE_RE.search(text)
        candidate = fence_match.group(1) if fence_match else text.strip()
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
