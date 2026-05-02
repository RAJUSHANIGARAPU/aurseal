"""Chaos resilience plugin — generates multi-fault scenarios from architecture analysis."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from plugins._base_plugin import BasePlugin, PluginPriority, PluginResult
from plugins.cost_governor import CostGovernor

_OUT_FILE = Path("ai_generated_tests/chaos/chaos_scenarios.md")


def _scan_imports(source_dir: Path) -> dict[str, list[str]]:
    """Return module -> imported modules map."""
    graph: dict[str, list[str]] = {}
    for py_file in source_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        if imports:
            graph[str(py_file)] = imports
    return graph


class ChaosResiliencePlugin(BasePlugin):
    name = "chaos-resilience"
    priority = PluginPriority.BACKGROUND
    trigger_conditions = ["manual"]

    def run(self, context: dict) -> PluginResult:
        source_dir = Path(context.get("source_dir", "."))
        governor = context.get("cost_governor") or CostGovernor()
        is_dry = context.get("dry_run", False)
        scenario_count = 2 if is_dry else 5

        import_graph = _scan_imports(source_dir)
        arch_summary = f"Project has {len(import_graph)} modules with cross-module dependencies."

        scenarios: list[str] = []
        try:
            from api.llm.claude_client import ClaudeLLMClient

            model = governor.get_model("claude-sonnet-4-6")
            llm = ClaudeLLMClient(model=model)
            prompt = (
                f"You are a chaos engineering expert. Given this architecture summary: "
                f"{arch_summary}\n\nGenerate {scenario_count} chaos/fault injection scenarios. "
                "Format each as: ## Scenario N: <title>\n<description>\n**Fault**: <fault>\n"
                "**Expected behavior**: <behavior>\n"
            )
            response = governor.cached_complete(prompt, llm.complete)
            if response and not response.startswith("Claude API error"):
                scenarios = response.strip().split("\n## ")
            else:
                scenarios = [
                    f"Scenario {i+1}: Network partition between services"
                    for i in range(scenario_count)
                ]
        except Exception:  # noqa: BLE001
            scenarios = [
                f"Scenario {i+1}: Service dependency failure" for i in range(scenario_count)
            ]

        if not is_dry:
            _OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            content = "# Chaos Scenarios\n\n" + "\n\n".join(
                f"## {s}" if not s.startswith("Scenario") else s for s in scenarios
            )
            _OUT_FILE.write_text(content, encoding="utf-8")

        return PluginResult(
            status="pass",
            findings=[{"scenarios": scenarios[:scenario_count], "scenario_count": len(scenarios)}],
            dry_run=is_dry,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    ctx: dict = {"source_dir": "."}
    plugin = ChaosResiliencePlugin()
    result = plugin.dry_run(ctx) if args.dry_run else plugin.execute(ctx)
    print(f"status={result.status} findings={result.findings}")
    sys.exit(0 if result.status in ("pass", "skip") else 1)
