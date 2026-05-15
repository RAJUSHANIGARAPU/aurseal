"""FixerAgent — diagnoses test failures and applies targeted code fixes."""

from __future__ import annotations

from core.agents.base_agent import AgentResult, BaseAgent
from core.ai.auto_fixer import AutoFixer

_SYSTEM_PROMPT = """You are a Playwright Python test automation expert.
Your job is to fix failing tests by calling the available tools.

Strategy:
1. Call suggest_fix to understand the root cause before applying changes.
2. Call fix_file to apply the fix directly to the file.
3. Call done when the fix is applied or you cannot proceed further.

Rules:
- Never apply a fix without understanding the error first.
- Use Playwright best practices: locator chaining, expect() assertions, proper waits.
- Never suggest time.sleep() — use page.wait_for_load_state() or expect().to_be_visible().
"""


class FixerAgent(BaseAgent):
    """Agent wrapping AutoFixer — uses Claude to decide fix strategy via tool_use."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        super().__init__(model)
        self._fixer = AutoFixer()
        self._register_tools()

    def fix(self, file_path: str, error_description: str, error_trace: str = "") -> AgentResult:
        return self.run(
            system_prompt=_SYSTEM_PROMPT,
            user_message=(
                f"Fix failures in: {file_path}\n\n"
                f"Error: {error_description}\n\n"
                f"Trace (truncated):\n{error_trace[:1500]}"
            ),
        )

    def _register_tools(self) -> None:
        self.register_tool(
            "suggest_fix",
            self._tool_suggest_fix,
            {
                "name": "suggest_fix",
                "description": "Generate a fix suggestion without applying it — returns root cause and diff",  # noqa: E501
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "error_description": {"type": "string"},
                        "error_trace": {"type": "string"},
                        "context_file": {
                            "type": "string",
                            "description": "Path to the file with the broken code",
                        },
                    },
                    "required": ["error_description"],
                },
            },
        )
        self.register_tool(
            "fix_file",
            self._tool_fix_file,
            {
                "name": "fix_file",
                "description": "Apply an AI-generated fix directly to a Python test or page object",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the Python file to fix",
                        },
                        "error_description": {"type": "string"},
                        "error_trace": {"type": "string"},
                    },
                    "required": ["file_path", "error_description"],
                },
            },
        )

    def _tool_suggest_fix(
        self,
        error_description: str,
        error_trace: str = "",
        context_file: str | None = None,
    ) -> dict:
        suggestion = self._fixer.suggest_fix(error_description, error_trace, context_file)
        return {"suggestion": suggestion}

    def _tool_fix_file(
        self,
        file_path: str,
        error_description: str,
        error_trace: str = "",
    ) -> dict:
        fixed = self._fixer.fix_file(file_path, error_description, error_trace)
        return {"fixed": fixed, "file": file_path}
