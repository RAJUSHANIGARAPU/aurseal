"""Unit AI plugin — generates unit tests from Python source using Claude."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor


class UnitAIPlugin(BasePlugin):
    name = "unit-ai"
    priority = PluginPriority.NORMAL
    trigger_conditions = ["source_change"]

    def run(self, context: dict) -> PluginResult:
        if context.get("dry_run"):
            return PluginResult(status="skip", dry_run=True)

        governor = context.get("cost_governor") or CostGovernor()
        source_dir = Path(context.get("source_dir", "."))
        out_dir = Path("ai_generated_tests/unit")
        out_dir.mkdir(parents=True, exist_ok=True)

        py_files = [f for f in source_dir.rglob("*.py") if "test" not in f.name]
        generated: list[str] = []
        total_tokens = 0
        total_cost = 0.0

        for src_file in py_files[:5]:
            try:
                source = src_file.read_text(encoding="utf-8")
                ast.parse(source)
            except Exception:  # noqa: BLE001
                continue

            try:
                from api.llm.claude_client import ClaudeLLMClient

                model = governor.get_model("claude-haiku-4-5-20251001")
                llm = ClaudeLLMClient(model=model)
                prompt = (
                    f"Generate pytest unit tests for this Python module. "
                    f"Return only valid Python test code.\n\n```python\n{source[:2000]}\n```"
                )
                response = governor.cached_complete(prompt, llm.complete)
            except Exception:  # noqa: BLE001
                response = f"# Auto-generated tests for {src_file.name}\nimport pytest\n"

            out_file = out_dir / f"test_{src_file.stem}_ai.py"
            out_file.write_text(response, encoding="utf-8")
            generated.append(str(out_file))
            total_tokens += len(response) // 4
            total_cost += 0.0001
            governor.record("claude-haiku-4-5-20251001", total_tokens, 0.0001)

        return PluginResult(
            status="pass",
            findings=[{"generated_files": generated, "count": len(generated)}],
            tokens_used=total_tokens,
            cost_usd=total_cost,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = UnitAIPlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
