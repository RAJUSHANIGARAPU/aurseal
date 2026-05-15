"""HealerAgent — heals broken locators and flaky tests via FailureAnalyzer + UIHealer."""

from __future__ import annotations

import json
from pathlib import Path

from api.llm.claude_client import ClaudeLLMClient
from autonomous_ui.analyzer import FailureAnalyzer
from autonomous_ui.healer import UIHealer
from autonomous_ui.models import FailureBundle
from core.agents.base_agent import AgentResult, BaseAgent

_SYSTEM_PROMPT = """You are a Playwright test healing expert.
Your job is to analyze failure bundles and apply healing strategies.

Strategy:
1. Call analyze_bundle to understand the root cause and failure type.
2. Call heal_test to apply the healing strategy (locator fix, timing fix, etc.).
3. Call done with status=passed if healing was applied, failed if not.

Failure types and preferred strategies:
- selector_not_found → fix the locator in the page object
- timeout → add explicit wait or increase timeout
- assertion_failed → check the expected value or state
- network_error → check environment config
"""


class HealerAgent(BaseAgent):
    """Agent wrapping FailureAnalyzer + UIHealer."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        super().__init__(model)
        llm = ClaudeLLMClient()
        self._analyzer = FailureAnalyzer(llm=llm)
        self._healer = UIHealer(llm=llm)
        self._register_tools()

    def heal(self, bundle_path: str) -> AgentResult:
        return self.run(
            system_prompt=_SYSTEM_PROMPT,
            user_message=f"Analyze and heal the failure bundle at: {bundle_path}",
        )

    def _register_tools(self) -> None:
        self.register_tool(
            "analyze_bundle",
            self._tool_analyze_bundle,
            {
                "name": "analyze_bundle",
                "description": "Load a failure bundle JSON and analyze root cause",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bundle_path": {
                            "type": "string",
                            "description": "Path to the failure bundle JSON file",
                        },
                    },
                    "required": ["bundle_path"],
                },
            },
        )
        self.register_tool(
            "heal_test",
            self._tool_heal_test,
            {
                "name": "heal_test",
                "description": "Apply a healing strategy — patches locators or test logic",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bundle_path": {
                            "type": "string",
                            "description": "Path to the failure bundle JSON file",
                        },
                        "dom_snapshot": {
                            "type": "string",
                            "description": "Optional DOM snapshot for locator healing",
                        },
                    },
                    "required": ["bundle_path"],
                },
            },
        )

    def _load_bundle(self, bundle_path: str) -> FailureBundle:
        data = json.loads(Path(bundle_path).read_text())
        return FailureBundle.from_dict(data)

    def _tool_analyze_bundle(self, bundle_path: str) -> dict:
        bundle = self._load_bundle(bundle_path)
        analysis = self._analyzer.analyze(bundle, use_llm=True)
        return {
            "test": analysis.test_name,
            "failure_type": analysis.failure_type.value,
            "root_cause": analysis.root_cause,
            "confidence": analysis.confidence,
            "suggestion": analysis.llm_suggestion,
        }

    def _tool_heal_test(self, bundle_path: str, dom_snapshot: str = "") -> dict:
        bundle = self._load_bundle(bundle_path)
        analysis = self._analyzer.analyze(bundle, use_llm=True)
        result = self._healer.heal(analysis, dom_snapshot=dom_snapshot)
        return {
            "strategy": result.strategy,
            "applied": result.applied,
            "details": result.details,
            "patched_files": [str(p) for p in result.patched_files],
        }
