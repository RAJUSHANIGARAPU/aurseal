"""
PlannerAgent: top-level orchestrator that uses Claude tool_use to decide
which action to take after a test run — analyze, fix, trace-analyze, generate, or done.
"""

from __future__ import annotations

from pathlib import Path

from core.agents.base_agent import AgentResult, BaseAgent
from core.agents.bus import AgentBus, default_bus
from core.ai.auto_fixer import AutoFixer
from core.ai.log_analyzer import LogAnalyzer
from core.ai.test_generator import TestGenerator
from core.ai.trace_analyzer import TraceAnalyzer

_SYSTEM_PROMPT = """You are an autonomous Playwright test automation agent.

You receive pytest run output and must resolve test failures using the available tools.

Strategy:
1. Always call analyze_failures first to understand what broke and why.
2. For selector/locator failures: call analyze_trace to get UI context, then fix_file.
3. For logic or assertion failures: call fix_file directly with the diagnosis.
4. For missing test coverage: call generate_test.
5. Call done when: all identified failures have been addressed, OR you have tried
   all applicable tools and cannot make further progress.

Rules:
- Extract the failing file path(s) from the pytest output before calling fix_file.
- Pass the full diagnosis text from analyze_failures as error_description to fix_file.
- status='passed' only if you believe the fixes applied will make tests pass.
- status='blocked' if ANTHROPIC_API_KEY is missing or tools keep returning errors.
- status='failed' if you tried everything but couldn't fix the failures.
"""

_ANALYZE_ONLY_SYSTEM_PROMPT = (
    _SYSTEM_PROMPT
    + """

IMPORTANT: --no-fix mode is active. Do NOT call fix_file or generate_test.
Only call analyze_failures and analyze_trace, then call done.
"""
)


class PlannerAgent(BaseAgent):
    """
    Receives pytest run output and decides what to do next via Claude tool_use.
    Publishes results to AgentBus topic 'planner.result'.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        bus: AgentBus | None = None,
    ) -> None:
        super().__init__(model)
        self._bus = bus or default_bus
        self._analyzer = LogAnalyzer()
        self._fixer = AutoFixer()
        self._trace = TraceAnalyzer()
        self._generator = TestGenerator()
        self._register_tools()

    def plan(
        self,
        run_output: str,
        exit_code: int,
        iteration: int,
        analyze_only: bool = False,
    ) -> AgentResult:
        system = _ANALYZE_ONLY_SYSTEM_PROMPT if analyze_only else _SYSTEM_PROMPT
        result = self.run(
            system_prompt=system,
            user_message=(
                f"pytest exited with code {exit_code} (iteration {iteration}).\n\n"
                f"pytest output (last 3000 chars):\n{run_output[-3000:]}\n\n"
                "Decide what actions to take. Call done when finished."
            ),
            max_turns=8,
        )
        self._bus.publish(
            "planner.result",
            {
                "iteration": iteration,
                "exit_code": exit_code,
                "status": result.status,
                "reason": result.reason,
                "actions": result.actions,
            },
        )
        return result

    def _register_tools(self) -> None:
        self.register_tool(
            "analyze_failures",
            self._tool_analyze_failures,
            {
                "name": "analyze_failures",
                "description": "Parse JUnit XML and pytest output to identify root causes of failures",  # noqa: E501
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reports_dir": {
                            "type": "string",
                            "description": "JUnit XML reports directory (default: reports)",
                        }
                    },
                    "required": [],
                },
            },
        )
        self.register_tool(
            "fix_file",
            self._tool_fix_file,
            {
                "name": "fix_file",
                "description": "Apply an AI-generated fix to a failing test or page object file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the Python file to fix",
                        },
                        "error_description": {
                            "type": "string",
                            "description": "Root cause from analyze_failures",
                        },
                        "error_trace": {
                            "type": "string",
                            "description": "Stack trace from the pytest output",
                        },
                    },
                    "required": ["file_path", "error_description"],
                },
            },
        )
        self.register_tool(
            "analyze_trace",
            self._tool_analyze_trace,
            {
                "name": "analyze_trace",
                "description": "Analyze the latest Playwright trace ZIP to identify UI failures",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "trace_path": {
                            "type": "string",
                            "description": "Path to trace ZIP file; omit to use the latest",
                        }
                    },
                    "required": [],
                },
            },
        )
        self.register_tool(
            "generate_test",
            self._tool_generate_test,
            {
                "name": "generate_test",
                "description": "Generate a new pytest test file from the latest Playwright trace",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "page_name": {
                            "type": "string",
                            "description": "Feature/page name for the generated test",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Where to write the generated test file",
                        },
                    },
                    "required": [],
                },
            },
        )

    def _tool_analyze_failures(self, reports_dir: str = "reports") -> dict:
        diagnosis = self._analyzer.analyze_failures(reports_dir)
        return {"diagnosis": diagnosis}

    def _tool_fix_file(
        self,
        file_path: str,
        error_description: str,
        error_trace: str = "",
    ) -> dict:
        fixed = self._fixer.fix_file(file_path, error_description, error_trace)
        return {"fixed": fixed, "file": file_path}

    def _tool_analyze_trace(self, trace_path: str = "") -> dict:
        resolved = Path(trace_path) if trace_path else self._trace.find_latest_trace()
        report = self._trace.analyze(resolved)
        return {"report": report}

    def _tool_generate_test(
        self,
        page_name: str = "wiki",
        output_path: str = "",
    ) -> dict:
        code = self._generator.generate_from_trace(page_name=page_name)
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(code)
        return {"generated": True, "path": output_path or "(preview)", "preview": code[:300]}
