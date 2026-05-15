"""GeneratorAgent — generates pytest tests from Playwright traces or spec files."""

from __future__ import annotations

from pathlib import Path

from core.agents.base_agent import AgentResult, BaseAgent
from core.ai.test_generator import TestGenerator

_SYSTEM_PROMPT = """You are a Playwright Python test generation expert.
Your job is to generate complete, production-quality pytest tests.

Strategy:
1. If given a trace ZIP path, call generate_from_trace.
2. If given a spec markdown path, call generate_from_spec.
3. Always specify an output_path so the test is written to disk.
4. Call done with status=passed when generation succeeds.

Generated tests must follow the project conventions:
- Use Page Object Model — UI logic in ui/pages/, tests in ui/tests/
- Locator strategy: data-testid > aria-label > role > text
- Never use time.sleep() — use expect().to_be_visible(timeout=N)
"""


class GeneratorAgent(BaseAgent):
    """Agent wrapping TestGenerator."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        super().__init__(model)
        self._generator = TestGenerator()
        self._register_tools()

    def generate(self, source: str, output_path: str | None = None) -> AgentResult:
        return self.run(
            system_prompt=_SYSTEM_PROMPT,
            user_message=(
                f"Generate a test from: {source}"
                + (f"\nWrite output to: {output_path}" if output_path else "")
            ),
        )

    def _register_tools(self) -> None:
        self.register_tool(
            "generate_from_trace",
            self._tool_generate_from_trace,
            {
                "name": "generate_from_trace",
                "description": "Generate a complete pytest test file from a Playwright trace ZIP",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "trace_path": {
                            "type": "string",
                            "description": "Path to trace ZIP; omit to use the latest",
                        },
                        "page_name": {
                            "type": "string",
                            "description": "Feature/page name, e.g. 'wiki_search'",
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
        self.register_tool(
            "generate_from_spec",
            self._tool_generate_from_spec,
            {
                "name": "generate_from_spec",
                "description": "Generate a pytest test from a spec markdown file in specs/",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "spec_path": {
                            "type": "string",
                            "description": "Path to the spec markdown file",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Where to write the generated test file",
                        },
                    },
                    "required": ["spec_path", "output_path"],
                },
            },
        )

    def _tool_generate_from_trace(
        self,
        trace_path: str = "",
        page_name: str = "wiki",
        output_path: str = "",
    ) -> dict:
        resolved = Path(trace_path) if trace_path else None
        code = self._generator.generate_from_trace(trace_path=resolved, page_name=page_name)
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(code)
        return {"generated": True, "path": output_path or "(preview)", "preview": code[:400]}

    def _tool_generate_from_spec(self, spec_path: str, output_path: str) -> dict:
        if hasattr(self._generator, "generate_from_spec"):
            code = self._generator.generate_from_spec(  # type: ignore[attr-defined]
                Path(spec_path), Path(output_path)
            )
        else:
            # Fallback: read spec as context, generate from latest trace
            code = self._generator.generate_from_trace(
                trace_path=None,
                page_name=Path(spec_path).stem,
            )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(code)
        return {"generated": True, "path": output_path}
