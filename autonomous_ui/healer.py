"""Applies healing strategies for classified UI test failures.

Healing strategies, in order of safety:
  locator_patch   — rewrites the failing entry in wiki_locators.json with an
                    LLM-suggested alternative selector, after checking the
                    suggestion is a shape core/base_page.py can resolve. Touches
                    only JSON, never test code — but that JSON is loaded by every
                    page object, so an unvalidated write breaks far more than the
                    test that failed (see _locator_shape_error).
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
  - the suggested locator is not a shape base_page.resolve() understands
  - the suggested locator is the value already in the registry (a no-op is not
    a fix, and reporting it as one made the heal log unreadable)

A heal that did nothing must never report applied=True. Everything above
returns strategy="none" with the reason, which is deliberately NOT the same as
"wait_retry": that strategy schedules `--reruns 2` on the next run, and a rerun
feeds the flakiness history.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from api.llm.base import BaseLLMClient
from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.models import FailureAnalysis, FailureType, HealingResult
from core.ai.auto_fixer import AutoFixer

_LOCATOR_REGISTRY = Path("ui/locators/wiki_locators.json")
_HEALING_OVERRIDES = Path("reports/healing_overrides.json")

# JSON block extractor — handles ```json fences from LLM responses
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# The reply is one small JSON object, but it echoes a locator entry plus a
# sentence of reasoning. At 512 a long reasoning string could cut the object in
# half, and a truncated reply used to come back as strategy="wait_retry",
# applied=True — a silent non-heal reported as a heal. Named here so the failure
# message can quote the actual limit.
_PATCH_MAX_TOKENS = 1024

# The locator shapes core/base_page.py:resolve() can turn into a Playwright
# locator, and what each one has to carry. Anything else raises there — for the
# whole page object, on every test using the key, not just the healed one.
_LOCATOR_VALUE_TYPES = ("css", "testid", "placeholder", "text")
_LOCATOR_TYPES = (*_LOCATOR_VALUE_TYPES, "role")


@dataclass(frozen=True)
class _PatchProposal:
    """What came back from the model, with "nothing" and "unusable" kept apart."""

    patch: dict | None = None
    # Non-empty when a response ARRIVED and could not be used. Distinct from
    # patch=None with no reason, which means the model was never reached.
    unusable: str = ""


def _locator_shape_error(locator: object) -> str:
    """Return why *locator* is not a registry entry, or "" when it is fine.

    The heal writes straight into wiki_locators.json, which every page object
    loads at construction, so an unvalidated write is not scoped to the test
    that failed. Probed: the model answered
    ``"new_locator": "[data-testid=search-field]"`` — a bare string where a
    mapping belongs — it was written verbatim, and core/base_page.py:16 then
    raised ``TypeError: string indices must be integers`` for every test using
    that key. A locator is cheap to check and expensive to get wrong.
    """
    if not isinstance(locator, dict):
        return f"expected a locator mapping, got {type(locator).__name__}"

    kind = locator.get("type")
    if not isinstance(kind, str) or not kind:
        return "locator has no 'type'"
    if kind not in _LOCATOR_TYPES:
        return f"unsupported locator type '{kind}' — base_page.resolve() would raise"

    if kind == "role":
        role = locator.get("role")
        if not isinstance(role, str) or not role.strip():
            return "role locator has no 'role'"
        return ""

    value = locator.get("value")
    if not isinstance(value, str) or not value.strip():
        return f"{kind} locator has no usable 'value'"
    return ""


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

        proposal = self._ask_llm_for_locator_patch(analysis, registry, dom_snapshot)
        if proposal.unusable:
            # A response arrived and could not be used. That is NOT "no heal
            # needed": the old code funnelled it into _record_retry, which
            # reports applied=True and "recorded for retry with --reruns 2",
            # so a truncated or malformed reply looked like a successful heal
            # and then poisoned the flakiness history through the reruns.
            return self._no_heal(analysis, f"locator patch rejected — {proposal.unusable}")
        if proposal.patch is None:
            # The model was never reached / said nothing at all. Genuinely no
            # answer, so retry-with-reruns is the honest fallback.
            return self._record_retry(analysis)

        patch = proposal.patch
        key = patch.get("locator_key", "")
        new_locator = patch.get("new_locator")

        if not key:
            return self._no_heal(analysis, "locator patch rejected — no 'locator_key' in response")
        if key not in registry:
            return self._no_heal(
                analysis, f"locator patch rejected — key '{key}' is not in the registry"
            )
        if new_locator is None:
            return self._no_heal(
                analysis, f"locator patch rejected — no 'new_locator' for key '{key}'"
            )

        shape_error = _locator_shape_error(new_locator)
        if shape_error:
            return self._no_heal(analysis, f"locator patch rejected — '{key}': {shape_error}")

        if new_locator == registry[key]:
            # The model handed back the value already in the registry. Writing
            # it changes nothing, and reporting applied=True with
            # "patched 'k': X → X" counted a no-op as a fix.
            return self._no_heal(
                analysis, f"locator '{key}' unchanged — the suggestion matches the current entry"
            )

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

    @staticmethod
    def _no_heal(analysis: FailureAnalysis, details: str) -> HealingResult:
        """Nothing was changed and nothing was scheduled — say exactly that."""
        return HealingResult(
            test_name=analysis.test_name,
            strategy="none",
            applied=False,
            details=details,
        )

    def _ask_llm_for_locator_patch(
        self, analysis: FailureAnalysis, registry: dict, dom_snapshot: str
    ) -> _PatchProposal:
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

        raw = self._llm.complete(prompt, max_tokens=_PATCH_MAX_TOKENS)
        if not raw or not raw.strip():
            return _PatchProposal()  # no answer at all

        parsed = self._parse_json(raw)
        if parsed is None:
            return _PatchProposal(unusable=self._describe_parse_failure(raw))
        return _PatchProposal(patch=parsed)

    @staticmethod
    def _describe_parse_failure(raw: str) -> str:
        """Name what came back, so a truncation is not filed as an outage.

        Truncation at max_tokens produced a response that opens a JSON object
        and never closes it. There was nothing in the old path that could tell
        that apart from the model declining, and both ended as a "successful"
        wait_retry.
        """
        candidate = raw.strip()
        fence = _JSON_FENCE_RE.search(candidate)
        if fence:
            candidate = fence.group(1).strip()
        if candidate.startswith("{") and not candidate.endswith("}"):
            return (
                f"response looks truncated at max_tokens={_PATCH_MAX_TOKENS} "
                f"({len(raw)} chars, unterminated JSON object)"
            )
        return f"response is not the JSON object we asked for ({len(raw)} chars)"

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
